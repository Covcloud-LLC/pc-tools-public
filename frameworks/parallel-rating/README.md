# Parallel rating framework

Reusable `.gs` source for manually ordered groups of rating steps in a PC
application. Each stage prepares its inputs serially, computes independent steps
concurrently, and applies the results serially in registration order. All applies
finish before the next stage prepares its inputs.

## Source files

- [ParallelRatingStep](src/pctools/rating/parallel/ParallelRatingStep.gs) defines
  `ParallelRatingStep<C, I, R>`: caller context, detached input, detached result.
- [ParallelFinalStepsOrchestrator](src/pctools/rating/parallel/ParallelFinalStepsOrchestrator.gs)
  defines `ParallelFinalStepsOrchestrator<C>`, its nested `Stage<C>`, and the
  adapters that let steps with different input/result types share a stage.

Copy the two files into the matching `pctools.rating.parallel` package under
your application's source root. Implement the interface using your own context,
snapshot and result types. The framework depends on standard Java collections
and concurrency APIs; no entity model or concrete context is required.

## Register steps

This template assumes your application supplies `MyRatingContext`, `context`,
`stepA`, `stepB`, and `dependentStep`:

```text
uses pctools.rating.parallel.ParallelFinalStepsOrchestrator

var orchestrator = new ParallelFinalStepsOrchestrator<MyRatingContext>(2)

var independent = orchestrator.addStage("Independent calculations")
independent.addStep("A", stepA)
independent.addStep("B", stepB)

orchestrator.addStage("Dependent calculation")
    .addStep("After A and B", dependentStep)

orchestrator.execute(context)
```

Each implementation provides `prepare(context): I`, `compute(input: I): R`,
and `apply(context, result)`. The application decides the stage dependencies.
An apply-only step may use `Void` for both input and result: return `null` from
prepare and compute, and perform its entity work in apply. A null result does
not suppress apply.

## Execution and failure behavior

- Configure and execute on the owning rating/bundle thread. Configuration is
  not safe for concurrent mutation. Each orchestrator executes once; create a
  new instance for each rating run and a separate step instance per registration.
- Only independent steps belong in one stage. Account for reads and writes in
  both serial phases when establishing dependencies.
- Input snapshots and results must be detached from entities and bundles.
  Compute must not access entities through input values, retained fields,
  getters, shared state, or closures. Generic types do not enforce this rule.
- The positive constructor argument caps workers per execution. Execute creates
  one fixed thread pool, reuses it across stages, and shuts it down in a finally
  block, waiting for termination. Concurrent executions each own a separate pool.
- Preparation failure starts no computation for that stage. Computation or
  submission failure drains accepted work and skips all of that stage's applies
  and all later stages. Interruption also drains work and restores the caller's
  interrupt flag before propagating failure.
- Apply failure stops subsequent applies and stages. Earlier writes are not
  undone by this framework; the caller owns transaction rollback. The framework
  neither opens nor commits a bundle.
- There is no overall execution or shutdown timeout. A worker that never
  returns can block execution indefinitely.

## Integration validation

Compile against your application's language/runtime version and verify that it
supports creating Java worker threads. Validate the entity/bundle boundary,
transaction rollback and worker count under realistic concurrent load before
production use. Source delivery does not establish compatibility or performance
for a particular PC environment.
