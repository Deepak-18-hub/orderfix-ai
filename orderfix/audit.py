import json
import logging


def event(step: str, correlation: str, result: str, destination: str = "coordinator") -> None:
    logging.getLogger("orderfix.audit").info(json.dumps({
        "event": step, "correlation_id": correlation, "source": "coordinator",
        "destination": destination, "result": result,
    }))
