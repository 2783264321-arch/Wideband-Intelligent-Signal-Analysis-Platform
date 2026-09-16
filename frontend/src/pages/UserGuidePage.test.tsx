import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { UserGuidePage } from "./UserGuidePage";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function renderGuide(locale: "zh-CN" | "en-US") {
  return render(
    <LocalizationProvider initialLocale={locale}>
      <ThemeProvider>
        <MemoryRouter>
          <UserGuidePage />
        </MemoryRouter>
      </ThemeProvider>
    </LocalizationProvider>,
  );
}

test("renders zh-CN guide content when locale is zh-CN", () => {
  renderGuide("zh-CN");
  expect(screen.getByText("分析单个 IQ 文件")).toBeInTheDocument();
});

test("renders en-US guide content when locale is en-US", () => {
  renderGuide("en-US");
  expect(screen.getByText("Analyze one IQ file")).toBeInTheDocument();
});
