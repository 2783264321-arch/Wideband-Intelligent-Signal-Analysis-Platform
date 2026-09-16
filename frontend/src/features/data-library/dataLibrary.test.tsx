import { render, screen } from "@testing-library/react";
import { DeleteConflictAlert } from "./DeleteConflictAlert";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

test("renders structured blockers safely", () => {
  render(
    renderWithLocalization(
      <DeleteConflictAlert
        blockers={[
          { kind: "active_analysis_run", resourceId: "run_1", reference: "analysis_run" },
          { kind: "dataset_evaluation", resourceId: "eval_1", reference: "recording" },
        ]}
      />,
    ),
  );
  const alert = screen.getByTestId("delete-conflict-alert");
  expect(alert).toHaveTextContent("active_analysis_run: run_1");
  expect(alert).toHaveTextContent("dataset_evaluation: eval_1");
});

test("renders nothing without blockers", () => {
  const { container } = render(renderWithLocalization(<DeleteConflictAlert blockers={[]} />));
  expect(container).toBeEmptyDOMElement();
});
