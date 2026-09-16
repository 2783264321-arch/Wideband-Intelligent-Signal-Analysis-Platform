import { expect, test } from "vitest";
import { sectionTitleKey } from "./sectionTitle";

test("maps routes to section titles", () => {
  expect(sectionTitleKey("/data-library")).toBe("nav.dataLibrary");
  expect(sectionTitleKey("/data-library/datasets/dsproj_x")).toBe("nav.dataLibrary");
  expect(sectionTitleKey("/experiments")).toBe("nav.experiments");
  expect(sectionTitleKey("/algorithm-lab")).toBe("nav.algorithmLab");
  expect(sectionTitleKey("/guide")).toBe("nav.guide");
  expect(sectionTitleKey("/settings")).toBe("nav.settings");
  expect(sectionTitleKey("/spectrum/rec_1")).toBe("spectrum.title");
});
