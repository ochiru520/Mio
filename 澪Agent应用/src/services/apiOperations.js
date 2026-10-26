/** Validate the finite operation segment of an otherwise fixed domain endpoint. */
export function selectOperation(operation, allowed) {
  if (!allowed.includes(operation)) throw new Error(`Unsupported API operation: ${operation}`)
  return operation
}
