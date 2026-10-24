"""Save debounce independent of Qt and watchdog for deterministic testing."""
class Stability:
    def __init__(self, interval=2):
        self.interval = interval
        self.states = {}

    def ready(self, name, signature, now):
        previous = self.states.get(name)
        if previous is None or previous[0] != signature:
            self.states[name] = (signature, now)
            return False
        return now - previous[1] >= self.interval
