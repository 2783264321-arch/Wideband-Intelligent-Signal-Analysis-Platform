import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AlgorithmLabPage } from "./AlgorithmLabPage";

afterEach(() => vi.unstubAllGlobals());

test("renders the Algorithm Lab case analysis controls through the page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const urlStr = String(url);
    if (urlStr.includes("/api/recordings?limit=")) {
      return new Response(JSON.stringify({ items: [], total: 0 }));
    }
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
  render(
    <MemoryRouter>
      <AlgorithmLabPage />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("Recording")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Compare" })).toBeInTheDocument();
});