# worker.py
import os
import ssl
import certifi
from redis import Redis
from rq import Worker, Queue, Connection

# which queues to listen on
listen = ["default"]

def make_redis_conn():
    # Heroku gives you a rediss:// URL; we disable cert validation here
    return Redis.from_url(
        os.environ["REDIS_URL"],
        ssl_cert_reqs=None,
        retry_on_timeout=True
    )

if __name__ == "__main__":
    redis_conn = make_redis_conn()
    with Connection(redis_conn):
        worker = Worker(map(Queue, listen))
        worker.work()
