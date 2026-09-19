import type { ExecutionMode, ExecutorSelection } from "../../api/types";

/** The user-facing execution environment value carried across the request boundary. */
export interface ExecutionEnvironmentValue {
  mode: ExecutionMode;
  /** Concrete executor when mode === "manual"; null for Auto. */
  executor: string | null;
}

export interface ExecutionEnvironmentSelectorProps {
  /** Backend projection; null while loading. */
  selection: ExecutorSelection | null;
  loading: boolean;
  error: string | null;
  value: ExecutionEnvironmentValue;
  onChange: (value: ExecutionEnvironmentValue) => void;
  disabled?: boolean;
  /** Hides the field label and the standing "not runnable" banner; the link
   *  remains so reasons are still reachable. Used on dense workspaces. */
  compact?: boolean;
  /** When false, never render the executor Select at all (execution is automatic
   *  and the environment is an internal detail); only diagnostics remain. */
  showSelector?: boolean;
}
