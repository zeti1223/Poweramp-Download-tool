import threading
import queue
import uuid

SEC_PER_CHECK = 2

class WorkerThread(threading.Thread):
    """A single worker that pulls callables from a queue and runs them."""

    def __init__(self, job_queue: queue.Queue, *, daemon: bool = True):
        super().__init__(daemon=daemon)
        self.job_queue = job_queue
        self.id = uuid.uuid4()

    def run(self) -> None:
        """Continuously fetch and execute jobs until the main program exits."""
        while True:
            try:
                # wait for a job; block only for a short time so we can be responsive
                job = self.job_queue.get(timeout=SEC_PER_CHECK)
            except queue.Empty:
                # Nothing to do – just try again
                continue

            try:
                job()  # Execute the callable
                print(f"{self.id} completed a job")
            finally:
                # Tell the queue that the job is done (important for `queue.join()`)
                self.job_queue.task_done()

class QueueSystem:
    """Thin wrapper around a thread pool and a job queue."""

    def __init__(self, max_threads: int = 4):
        self.job_queue = queue.Queue()
        self.workers = []

        for _ in range(max_threads):
            worker = WorkerThread(self.job_queue)
            worker.start()
            self.workers.append(worker)

    def submit_jobs(self, jobs: list):
        """Enqueue a list of callables to be executed by the pool."""
        for job in jobs:
            if callable(job):
                self.job_queue.put(job)
            else:
                raise TypeError("All jobs must be callables")

    def wait_completion(self):
        """Block until all queued jobs have finished."""
        self.job_queue.join()