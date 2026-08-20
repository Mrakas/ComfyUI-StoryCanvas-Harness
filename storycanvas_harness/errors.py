class StoryCanvasError(RuntimeError):
    """Base exception for a user-actionable StoryCanvas failure."""


class PolicyViolation(StoryCanvasError):
    """Raised when execution would exceed the explicit call or cost policy."""


class ProviderError(StoryCanvasError):
    """Raised when a configured provider cannot produce a valid response."""


class ResumeConflict(StoryCanvasError):
    """Raised when persisted task identity conflicts with the requested input."""


class WorkflowCompileError(StoryCanvasError):
    """Raised when a validated CanvasPlan cannot compile to a safe graph."""
