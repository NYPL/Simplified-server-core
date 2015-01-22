from nose.tools import set_trace
import datetime
import time

from model import (
    get_one_or_create,
    Timestamp,
)

class Monitor(object):

    ONE_MINUTE_AGO = datetime.timedelta(seconds=60)

    def __init__(
            self, _db, name, interval_seconds=1*60,
            default_start_time=None):
        self._db = _db
        self.service_name = name
        self.interval_seconds = interval_seconds
        self.stop_running = False
        if not default_start_time:
             default_start_time = (
                 datetime.datetime.utcnow() - self.ONE_MINUTE_AGO)
        self.default_start_time = default_start_time

    def run(self):        
        self.timestamp, new = get_one_or_create(
            self._db, Timestamp,
            service=self.service_name,
            create_method_kwargs=dict(
                timestamp=self.default_start_time
            )
        )
        start = self.timestamp.timestamp or self.default_start_time

        while not self.stop_running:
            cutoff = datetime.datetime.utcnow()
            new_timestamp = self.run_once(start, cutoff) or cutoff
            duration = datetime.datetime.utcnow() - cutoff
            to_sleep = self.interval_seconds-duration.seconds-1
            self.cleanup()
            self.timestamp.timestamp = new_timestamp
            self._db.commit()
            if to_sleep > 0:
                time.sleep(to_sleep)
            start = new_timestamp

    def run_once(self, start, cutoff):
        raise NotImplementedError()

    def cleanup(self):
        pass
        

class PresentationReadyMonitor(self):
    """A monitor that makes works presentation ready.

    This works by having a big list of CoverageProviders, and calling
    ensure_coverage() on each for the currently active edition of each
    work. If all the ensure_coverage() calls succeed, presentation of
    the work is calculated and the work is marked presentation ready.
    """
    # TODO after finishing refactoring
    pass
