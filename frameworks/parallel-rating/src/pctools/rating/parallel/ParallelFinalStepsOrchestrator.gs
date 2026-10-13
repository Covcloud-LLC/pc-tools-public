package pctools.rating.parallel

uses java.lang.Void
uses java.util.ArrayList
uses java.util.concurrent.Callable
uses java.util.concurrent.ExecutionException
uses java.util.concurrent.ExecutorService
uses java.util.concurrent.Executors
uses java.util.concurrent.Future
uses java.util.concurrent.TimeUnit
uses java.util.concurrent.atomic.AtomicBoolean

/**
 * Executes manually ordered stages using ParallelRatingStep<C, I, R>.
 * Each stage: prepare all serially -> compute all concurrently -> apply all serially.
 * The next stage is prepared only after the previous stage has finished applying.
 *
 * Configure and execute on the owning rating/bundle thread. Create a new instance
 * per rating execution. Steps in one stage must be independent, and each step
 * instance must belong to only one registration for this rating execution.
 * Input snapshots and results must be detached from entities and bundles;
 * compute must not access entities, including through state retained by a step.
 *
 * execute creates one fixed thread pool for this rating execution, reuses it
 * across stages, and shuts it down in finally. No pool is created if the instance
 * is merely configured. The worker count is per execution, not application-wide;
 * size it with the number of concurrent rating executions in mind.
 * This class does not open or commit a bundle. Confirm that creating standard
 * Java executor threads is supported in the target PC environment.
 *
 * A prepare/compute failure prevents ALL applies for that stage. Submitted jobs
 * are drained even on interruption; interruption is restored before throwing.
 * There is no timeout: a stuck compute can block execute indefinitely.
 * An apply failure stops later steps/stages, but does not undo earlier applies.
 * The caller must propagate failure and use its existing transaction rollback.
 */
class ParallelFinalStepsOrchestrator<C> {
  private var _parallelism: int
  private var _stages: ArrayList<Stage<C>> = new ArrayList<Stage<C>>()
  private var _started = new AtomicBoolean(false)

  construct(parallelism: int) {
    if (parallelism < 1) {
      throw new IllegalArgumentException("parallelism must be at least 1")
    }
    _parallelism = parallelism
  }

  function addStage(name: String): Stage<C> {
    checkConfigurable()
    if (name == null or name.trim().isEmpty()) {
      throw new IllegalArgumentException("stage name is required")
    }
    var stage = new Stage<C>(this, name)
    _stages.add(stage)
    return stage
  }

  function execute(context: C) {
    if (context == null) {
      throw new IllegalArgumentException("context is required")
    }
    if (not _started.compareAndSet(false, true)) {
      throw new IllegalStateException("Create a new orchestrator for each rating execution")
    }
    checkInterrupted()
    var executor = Executors.newFixedThreadPool(_parallelism)
    try {
      for (stage in _stages) {
        executeStage(stage, context, executor)
      }
    } finally {
      shutdownAndAwaitTermination(executor)
    }
    // Also propagate interruption received during otherwise successful cleanup.
    checkInterrupted()
  }

  private function shutdownAndAwaitTermination(executor: ExecutorService) {
    executor.shutdown()
    var interrupted = false
    try {
      while (not executor.isTerminated()) {
        try {
          executor.awaitTermination(1, TimeUnit.SECONDS)
        } catch (e: InterruptedException) {
          interrupted = true
          // Finish cleanup before restoring the caller's interrupt flag.
        }
      }
    } finally {
      if (interrupted) {
        Thread.currentThread().interrupt()
      }
    }
  }

  private function checkConfigurable() {
    if (_started.get()) {
      throw new IllegalStateException("Cannot change stages after execution starts")
    }
  }

  private function checkInterrupted() {
    if (Thread.currentThread().isInterrupted()) {
      // isInterrupted preserves the flag.
      throw new InterruptedException("Rating orchestration interrupted")
    }
  }

  private function executeStage(stage: Stage<C>, context: C,
                               executor: ExecutorService) {
    var prepared = new ArrayList<PreparedStep<C>>()
    for (registration in stage._steps) {
      checkInterrupted()
      // Finish ALL preparation before submitting any work in this stage.
      prepared.add(registration.prepare(context))
    }

    var futures = new ArrayList<Future<Void>>()
    var failure: RuntimeException = null
    var interrupted = false

    for (job in prepared) {
      if (Thread.currentThread().isInterrupted()) {
        interrupted = true
        failure = recordFailure(failure, new RuntimeException(
            "Interrupted while submitting stage " + stage._name, new InterruptedException()))
        break
      }
      try {
        futures.add(executor.submit(job))
      } catch (e: RuntimeException) {
        failure = recordFailure(failure, new RuntimeException(
            "Could not submit step " + job.Name + " in stage " + stage._name, e))
        break
      }
    }

    // Drain every accepted job before returning or applying. Do not cancel:
    // cancellation does not guarantee that a worker has actually stopped.
    for (future in futures index i) {
      var finished = false
      while (not finished) {
        try {
          future.get()
          finished = true
        } catch (e: InterruptedException) {
          if (not interrupted) {
            failure = recordFailure(failure, new RuntimeException(
                "Interrupted while waiting for stage " + stage._name, e))
          }
          interrupted = true
          // get cleared the interrupt flag. Continue draining, then restore it.
        } catch (e: ExecutionException) {
          failure = recordFailure(failure, new RuntimeException(
              "Compute failed for step " + prepared.get(i).Name + " in stage " + stage._name,
              e.getCause()))
          finished = true
        }
      }
    }

    if (interrupted) {
      Thread.currentThread().interrupt()
    }
    if (failure != null) {
      throw failure
    }

    // Future.get is the completion/visibility barrier for each stored result.
    // Null results are valid: apply-only steps must still be applied.
    for (job in prepared) {
      checkInterrupted()
      job.apply(context)
    }
  }

  private function recordFailure(first: RuntimeException, next: RuntimeException): RuntimeException {
    if (first == null) {
      return next
    }
    first.addSuppressed(next)
    return first
  }

  /** A manually defined group of independent steps, applied in registration order. */
  static class Stage<C> {
    private var _owner: ParallelFinalStepsOrchestrator<C>
    private var _name: String
    private var _steps: ArrayList<RegisteredStep<C>> = new ArrayList<RegisteredStep<C>>()

    private construct(owner: ParallelFinalStepsOrchestrator<C>, name: String) {
      _owner = owner
      _name = name
    }

    reified function addStep<I, R>(name: String, step: ParallelRatingStep<C, I, R>): Stage<C> {
      _owner.checkConfigurable()
      if (name == null or name.trim().isEmpty() or step == null) {
        throw new IllegalArgumentException("step name and step are required")
      }
      _steps.add(new TypedRegisteredStep<C, I, R>(name, step))
      return this
    }
  }

  // Typed adapters allow heterogeneous I/R pairs without unchecked result casts.
  private static abstract class RegisteredStep<C> {
    abstract function prepare(context: C): PreparedStep<C>
  }

  private static class TypedRegisteredStep<C, I, R> extends RegisteredStep<C> {
    private var _name: String
    private var _step: ParallelRatingStep<C, I, R>

    construct(name: String, step: ParallelRatingStep<C, I, R>) {
      _name = name
      _step = step
    }

    override function prepare(context: C): PreparedStep<C> {
      return new TypedPreparedStep<C, I, R>(_name, _step, _step.prepare(context))
    }
  }

  private static abstract class PreparedStep<C> implements Callable<Void> {
    abstract property get Name(): String
    abstract function apply(context: C)
  }

  private static class TypedPreparedStep<C, I, R> extends PreparedStep<C> {
    private var _name: String
    private var _step: ParallelRatingStep<C, I, R>
    private var _input: I
    private var _result: R

    construct(name: String, step: ParallelRatingStep<C, I, R>, input: I) {
      _name = name
      _step = step
      _input = input
    }

    override property get Name(): String {
      return _name
    }

    override function call(): Void {
      _result = _step.compute(_input)
      return null
    }

    override function apply(context: C) {
      _step.apply(context, _result)
    }
  }
}
