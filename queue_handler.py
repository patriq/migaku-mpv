import threading


class QueueHandler:
    def __init__(self):
        self.data_queues = []
        self.data_queues_lock = threading.Lock()

    def add_queue(self, q):
        with self.data_queues_lock:
            self.data_queues.append(q)

    def remove_queue(self, q):
        with self.data_queues_lock:
            self.data_queues.remove(q)

    def send_data(self, data):
        with self.data_queues_lock:
            for q in self.data_queues:
                q.put(data)
