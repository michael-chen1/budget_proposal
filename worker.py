# worker.py

import os
from redis import Redis
from rq.worker import Worker
from rq.queue import Queue

# Which queues to listen on
listen = ["default"]

def make_redis_conn():
    """
    Build a Redis connection that skips certificate validation,
    because Heroku Redis uses a self‑signed cert.
    """
    return Redis.from_url(
        os.environ["REDIS_URL"],  # rediss://… on Heroku
        ssl_cert_reqs=None,       # disable SSL cert verification
        retry_on_timeout=True,
    )

if __name__ == "__main__":
    # 1) Create the Redis connection
    conn = make_redis_conn()

    # 2) Build RQ Queue objects bound to that connection
    queues = [Queue(name, connection=conn) for name in listen]

    # 3) Instantiate a Worker to watch those queues
    worker = Worker(queues)

    # 4) Start the work loop
    worker.work()
