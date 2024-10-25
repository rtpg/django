def from_codegen(f):
    """
    This indicates that the function was gotten from codegen, and
    should not be directly modified
    """
    return f


def generate_unasynced_codegen(f):
    """
    This indicates we should unasync this function/method
    """
    return f


# this marker gets replaced by False when unasyncifying a function
ASYNC_TRUTH_MARKER = True
