"""Base classes."""

REGISTRY = {}


def register(name):
    def deco(cls):
        REGISTRY[name] = cls
        return cls
    return deco


class Base:
    """Template-method base."""

    def run(self, x):
        y = self.prepare(x)
        return self.step(y)

    def prepare(self, x):
        return x

    def step(self, y):
        raise NotImplementedError
