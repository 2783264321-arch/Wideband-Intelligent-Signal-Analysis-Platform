import { useLocalization } from "../../localization/useLocalization";
import { guideMarkdownFor } from "./guideContent";
import { renderMarkdown } from "./renderMarkdown";

export function UserGuideContent() {
  const { locale } = useLocalization();
  const html = renderMarkdown(guideMarkdownFor(locale));
  return <div className="wisa-guide" data-testid="user-guide-content" dangerouslySetInnerHTML={{ __html: html }} />;
}
