from util.problem_detail import ProblemDetail as pd

INVALID_INPUT = pd(
      "http://librarysimplified.org/terms/problem/invalid-input",
      400,
      "Invalid input.",
      "You provided invalid or unrecognized input.",
)

UNRECOGNIZED_DATA_SOURCE = pd(
      "http://librarysimplified.org/terms/problem/unrecognized-data-source",
      400,
      "Unrecognized data source.",
      "I don't know anything about that data source.",
)
