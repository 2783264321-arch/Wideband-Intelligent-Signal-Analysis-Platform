import type { MessageKey } from "../localization/types";

export function sectionTitleKey(pathname: string): MessageKey {
  if (pathname.startsWith("/data-library")) return "nav.dataLibrary";
  if (pathname.startsWith("/experiments")) return "nav.experiments";
  if (pathname.startsWith("/algorithm-lab")) return "nav.algorithmLab";
  if (pathname.startsWith("/guide")) return "nav.guide";
  if (pathname.startsWith("/settings")) return "nav.settings";
  if (pathname.startsWith("/spectrum") || pathname.startsWith("/signals")) return "spectrum.title";
  return "app.title";
}
