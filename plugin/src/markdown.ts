import MarkdownIt from "markdown-it";

// Saved pages and model output are untrusted. Rendering must not load media or HTML.
const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
});
markdown.validateLink = (url) => /^https?:\/\//i.test(url);
markdown.renderer.rules.image = (tokens, index) =>
  markdown.utils.escapeHtml(tokens[index].content);
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  const token = tokens[index];
  token.attrSet("data-source", "");
  token.attrSet("target", "_blank");
  token.attrSet("rel", "noopener noreferrer");
  return renderer.renderToken(tokens, index, options);
};

export const renderMarkdown = (text: string) => markdown.render(text);
