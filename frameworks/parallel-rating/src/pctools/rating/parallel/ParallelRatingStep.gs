package pctools.rating.parallel

/**
 * A rating step with caller-owned context C, detached input I, and detached result R.
 * C is available only to the serial phases; compute must use only its input.
 * Steps must not retain entities or context for access from compute.
 */
interface ParallelRatingStep<C, I, R> {
  // Called on the owning rating/bundle thread before this stage's work starts.
  function prepare(context: C): I

  // Called on a worker. Input and result must not contain entity/bundle references.
  function compute(input: I): R

  // Called on the owning rating/bundle thread after all stage computations succeed.
  // A null result is valid and must still be applied (for example, Void/Void steps).
  function apply(context: C, result: R)
}
