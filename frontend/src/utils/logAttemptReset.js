export function shouldResetLogViewer(previousTask, nextTask) {
  if (!previousTask || !nextTask) {
    return false
  }

  const enteredRunning = previousTask.status !== 'running' && nextTask.status === 'running'
  const attemptChanged = previousTask.attempt !== nextTask.attempt
  const startedAtChanged =
    Boolean(nextTask.started_at) && previousTask.started_at !== nextTask.started_at

  return enteredRunning || attemptChanged || startedAtChanged
}
