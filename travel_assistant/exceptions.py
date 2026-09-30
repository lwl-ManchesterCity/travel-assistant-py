"""对应 Java exception/TripPlanningException.java。"""


class TripPlanningException(Exception):
    def __init__(self, message: str):
        super().__init__(message)
