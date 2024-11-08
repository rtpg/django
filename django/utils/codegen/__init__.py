from functools import wraps


def _identity(f):
    return f


def from_codegen(f):
    """
    This indicates that the function was gotten from codegen, and
    should not be directly modified
    """
    return f


TRACKING_UNASYNCED = True
if TRACKING_UNASYNCED:

    def generate_unasynced(sync_variant=None, async_unsafe=False):
        def wrapper(f):
            @wraps(f)
            def wrapped(*args, **kwargs):
                assert False, "IN OLD ASYNC HELPER"
                return f(*args, **kwargs)

            return wrapped

        return wrapper

else:

    def generate_unasynced(sync_variant=None, async_unsafe=False):
        """
        This indicates we should unasync this function/method

        async_unsafe indicates whether to add the async_unsafe decorator
        """

        def wrapper(f):
            return f

        return wrapper


# this marker gets replaced by False when unasyncifying a function
ASYNC_TRUTH_MARKER = True
