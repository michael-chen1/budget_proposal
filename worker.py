# worker.py
##
##import os
##from dotenv import load_dotenv
##load_dotenv()
##
##from redis import Redis
##from rq.worker import Worker
##from rq.queue import Queue
##
### Which queues to listen on
##listen = ["default"]
##
##def make_redis_conn():
##    """
##    Build a Redis connection that skips certificate validation,
##    because Heroku Redis uses a self‑signed cert.
##    """
##    return Redis.from_url(
##        os.environ["REDIS_URL"],  # rediss://… on Heroku
##    )
##
##if __name__ == "__main__":
##    # 1) Create the Redis connection
##    conn = make_redis_conn()
##
##    # 2) Build RQ Queue objects bound to that connection
##    queues = [Queue(name, connection=conn) for name in listen]
##
##    # 3) Instantiate a Worker to watch those queues
##    worker = Worker(queues)
##
##    # 4) Start the work loop
##    worker.work()


# worker.py

import os
from dotenv import load_dotenv

# 1) Load your .env so REDIS_URL is in os.environ
load_dotenv()

from redis import Redis
from rq.queue import Queue
from rq.worker import SimpleWorker

# queues to listen on
listen = ["default"]
print(9)
def make_redis_conn():
    """
    Build a Redis connection (will pick up rediss:// on Heroku,
    redis:// on localhost, etc., from your REDIS_URL env var).
    """
    return Redis.from_url(os.environ["REDIS_URL"])

if __name__ == "__main__":
    # 2) Create the Redis connection
    print(3)
    conn = make_redis_conn()

    # 3) Build Queue objects bound to that connection
    print(4)
    queues = [Queue(name, connection=conn) for name in listen]

    # 4) Instantiate SimpleWorker to watch those queues
    print(5)
    worker = SimpleWorker(queues, connection=conn)

    print(f"Starting SimpleWorker, listening on queues: {listen}")
    # 5) Start processing jobs
    worker.work()
