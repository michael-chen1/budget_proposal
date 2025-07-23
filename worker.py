# worker.py

import os
import ssl
import certifi
from redis import Redis
from rq.worker import Worker
from rq.queue import Queue
from rq.connection import Connection

# Queues to listen on
listen = ["default"]

def make_redis_conn():
    """
    Build a Redis connection that skips certificate verification
    (Heroku’s Redis uses a self‑signed cert).
    """
    return Redis.from_url(
        os.environ["REDIS_URL"],    # should be rediss://… on Heroku
        ssl_cert_reqs=None,         # disable SSL certificate validation
        retry_on_timeout=True,      # retry commands if Redis is busy
    )

if __name__ == "__main__":
    # Create the Redis connection
    redis_conn = make_redis_conn()

    # Bind our connection into RQ’s context
    with Connection(redis_conn):
        # Instantiate a worker to process jobs from the named queues
        worker = Worker(list(map(Queue, listen)))
        # Start the work loop
        worker.work()
