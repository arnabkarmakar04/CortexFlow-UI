from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(
        min_length=1,
        description=(
            "Unique identifier for one distinct objective in the current task plan, such as 'task_1' or 'task_2'. "
            "Each task_id must be unique and is used only to reference that objective during the current execution."
        )
    )
    objective: str = Field(
        min_length=1,
        description=(
            "One distinct meaningful outcome the user expects for the current request. "
            "Describe what must be achieved, not how it is implemented. "
            "Do not describe a tool call, API operation, parameter, implementation step, intermediate reasoning, or graph operation."
        )
    )
    execution_mode: Literal["MCP", "RAG", "DIRECT"] = Field(
        description=(
            "'MCP' means the objective requires information obtained from an external system or source, or requires an external operation, through the system's MCP capability. "
            "'RAG' means the objective requires information from the configured domain-specific knowledge base, such as internal company documentation, policies, rules, regulations, procedures, or other indexed organizational knowledge. "
            "'DIRECT' means the objective can be fulfilled from the current request, reliable relevant conversation context, explicitly provided information, model capabilities, or deterministic reasoning without MCP or RAG."
        )
    )
    dependencies: list[str] = Field(
        description=(
            "Task IDs of prerequisite MCP objectives whose successful execution results are required before this MCP objective can execute. "
            "Dependencies are MCP-to-MCP execution prerequisites only, and every dependency must reference an existing MCP task that appears earlier in the ordered task plan. "
            "Do not use dependencies for DIRECT or RAG objectives, final-answer information flow, conceptual relationships, wording order, or temporal expressions by themselves. "
            "Use an empty list when no MCP prerequisite is required."
        )
    )

class OrchestratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    need_clarification: bool = Field(
        description=(
            "True only when the user's core intent, an essential reference, or genuinely conflicting instructions cannot be resolved reliably from the current request and relevant conversation context. "
            "False when the request is sufficiently clear to proceed. "
            "Do not request clarification for unresolved tool, parameter, temporal, or downstream implementation details."
        )
    )
    clarification_reason: str = Field(
        description=(
            "Concise explanation of the unresolved ambiguity when clarification is required. "
            "When clarification is not required, state that no clarification is required."
        )
    )
    tasks: list[TaskSpec] = Field(
        description=(
            "Complete decomposition of the current user request into distinct meaningful objectives, with each genuine objective represented exactly once. "
            "Return tasks in dependency-respecting order: every MCP prerequisite must appear earlier than the MCP objective that depends on it. "
            "For independent objectives, preserve a deterministic order based primarily on the order in which the user's objectives are introduced. "
            "Task-plan order defines dependency order, not mandatory sequential execution; independent MCP objectives may execute concurrently when their required information is available. "
            "Do not create tasks for parameters, implementation steps, internal reasoning, tool selection, graph operations, or information already available from the current request or reliable relevant conversation context unless that information itself must be obtained through MCP or RAG. "
            "When clarification is required, return an empty task list."
        )
    )

    execution_guidance: str = Field(
        description=(
            "Directive guidance for the downstream MCP Planner. "
            "Identify every MCP objective, distinguish immediately executable independent MCP objectives from objectives blocked by prerequisites, and describe every MCP-to-MCP information dependency. "
            "For each dependency, identify the prerequisite task, dependent task, and information that must flow from the successful prerequisite result. "
            "Carry relevant information established in conversation history when it is required for MCP execution, provided the current request clearly makes that information relevant. "
            "State that a dependent objective must wait until its required prerequisite information is successfully available. "
            "Do not select tools, formulate tool arguments, specify APIs, resolve downstream temporal values, or describe implementation details."
        )
    )

    # Validator to ensure the task plan structure is valid based on the need_clarification flag and task dependencies
    @model_validator(mode="after")
    def validate_plan_structure(self):
        if self.need_clarification:
            if self.tasks:
                raise ValueError("When need_clarification is true, tasks must be an empty list.\n")
            return self
        
        if not self.tasks:
            raise ValueError("When need_clarification is false, tasks must contain at least one objective.\n")

        task_ids = [task.task_id for task in self.tasks]

        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique within the task plan.\n")

        task_index = {task_id: index for index, task_id in enumerate(task_ids) }
        task_modes = {task.task_id: task.execution_mode for task in self.tasks }

        for index, task in enumerate(self.tasks):
            if task.dependencies and task.execution_mode != "MCP":
                raise ValueError(f"Task '{task.task_id}' has dependencies but its execution_mode is '{task.execution_mode}'. Only MCP tasks may have dependencies.\n")

            for dependency_id in task.dependencies:
                if dependency_id not in task_index:
                    raise ValueError(f"Task '{task.task_id}' references unknown dependency '{dependency_id}'.\n")

                if task_modes[dependency_id] != "MCP":
                    raise ValueError(f"Task '{task.task_id}' references dependency '{dependency_id}', which is not an MCP task.\n")

                if task_index[dependency_id] >= index:
                    raise ValueError(f"Task '{task.task_id}' references dependency '{dependency_id}' that does not appear earlier in the ordered task plan.\n")
        return self

class RewrittenQueryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: Literal["merge", "context_switch", "cancel"] = Field(
        description=(
            "'merge' when the user continues the original request by providing clarification, missing information, corrections, or additional constraints. "
            "'context_switch' when the user abandons the original request and starts a different request. "
            "'cancel' when the user explicitly cancels or stops the current request."
        )
    )

    rewritten_query: str = Field(
        description=(
            "For 'merge', return the original query updated only with information explicitly provided by the user. "
            "Preserve the original objective and all unchanged information. "
            "For 'context_switch' or 'cancel', return exactly the user's response."
        )
    )

class ObjectiveCompletionEvaluatorSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_status: Literal["complete", "continue"] = Field(
        description=(
            "'complete' when all MCP-required objectives are sufficiently fulfilled by the actual successful MCP execution results, including valid empty-result outcomes, or when no remaining MCP objective requires additional execution and remaining work can be handled without further MCP execution. "
            "'continue' only when a remaining MCP objective genuinely requires another MCP execution and all required MCP prerequisites are already satisfied."
        )
    )
    reasoning: str = Field(
        description=(
            "Concise, evidence-based guidance derived only from the Original User Query, authoritative Task Plan, Execution Guidance, and actual Execution History. "
            "For 'continue', identify the remaining MCP objective, the successful prerequisite result or results that make it executable, and the relevant information the Planner must preserve. "
            "For 'complete', state that no further MCP execution is required and, when applicable, identify remaining DIRECT or final-synthesis work. "
            "Do not invent results, dependencies, requirements, tool selections, arguments, capabilities, or user information."
        )
    )
