"""Read-only provider polling and local follow-up maintenance."""
import logging
import threading


class Scheduler:
    def __init__(self, database):
        self.database = database
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='DP-acompanhamento')

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self):
        from . import absence_workflow, extension_workflow
        while not self.stop_event.is_set():
            for action in (lambda: absence_workflow.run_due(self.database),
                           lambda: extension_workflow.run_due(self.database, self.database.parent / 'private-registry-documents', limit=3)):
                if self.stop_event.is_set():
                    break
                try:
                    action()
                except Exception as error:
                    logging.getLogger(__name__).warning('Acompanhamento operacional aguardará nova tentativa (%s).', type(error).__name__)
            self.stop_event.wait(30)
