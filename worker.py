# worker.py

import os
import ssl
import certifi
from redis import Redis
from rq.worker import Worker
from rq.queue import Queue
from rq.connections import Connection

# Which queues to listen on
listen = ["default"]

def make_redis_conn():
    """
    Build a Redis connection that skips cert validation,
    because Heroku Redis uses a self‑signed certificate.
    """
    return Redis.from_url(
        os.environ["REDIS_URL"],  # Heroku puts a rediss:// URL here
        ssl_cert_reqs=None,       # disable certificate checking
        retry_on_timeout=True,    # retry if Redis is busy
    )

if __name__ == "__main__":
    # Establish our Redis connection
    redis_conn = make_redis_conn()
    # Bind that connection for our Worker
    with Connection(redis_conn):
        # Create a Worker that listens on the named queues
        worker = Worker(list(map(Queue, listen)))
        # Start processing jobs
        worker.work()
