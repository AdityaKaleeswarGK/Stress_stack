import json
from orchard import Basket as Box
from .model import double as twice
from . import model


def build(count):
    """Build a basket, exercising a class alias and a function alias."""
    return Box(twice(count))


def describe(count):
    return json.dumps({"count": model.double(count)})


def shadow(twice):
    return twice(3)


def nested():
    from .model import double as local_double

    def run():
        return local_double(4)

    return run()
