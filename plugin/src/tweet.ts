import { createElement as h, useState, type ImgHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import {
  TweetContainer,
  TweetBody,
  TweetMedia,
  enrichTweet,
} from "react-tweet";
import type { Tweet, MediaDetails } from "react-tweet/api";

type Entity = {
  start: number;
  end: number;
  url?: string;
  expanded_url?: string;
  display_url?: string;
  username?: string;
  tag?: string;
};
type Media = {
  media_key: string;
  type: string;
  url?: string;
  preview_image_url?: string;
  width?: number;
  height?: number;
  alt_text?: string;
  variants?: { url: string; content_type: string; bit_rate?: number }[];
};
export type PostCard = {
  post: {
    id: string;
    text?: string;
    created_at?: string;
    entities?: { urls?: Entity[]; mentions?: Entity[]; hashtags?: Entity[] };
    note_tweet?: { text: string; entities?: PostCard["post"]["entities"] };
    article?: { title?: string; plain_text?: string };
    public_metrics?: Record<string, number>;
  };
  user: { name?: string; username?: string; profile_image_url?: string };
  media: Media[];
  relation?: "replied_to" | "quoted" | null;
};
const escape = (text: string) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const webUrl = (value = "") => {
  try {
    const u = new URL(value);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : "";
  } catch {
    return "";
  }
};
const mediaUrl = (value = "") => {
  try {
    const u = new URL(value);
    return u.protocol === "https:" &&
      ["pbs.twimg.com", "video.twimg.com"].includes(u.hostname)
      ? u.href
      : "";
  } catch {
    return "";
  }
};

function imageMedia(m: Media): MediaDetails {
  const width = m.width || 1200,
    height = m.height || 800;
  const size = { w: width, h: height, resize: "fit" };
  return {
    type: "photo",
    media_url_https: mediaUrl(m.url),
    ext_alt_text: m.alt_text,
    display_url: "",
    expanded_url: "",
    indices: [0, 0],
    url: "",
    ext_media_availability: { status: "Available" },
    ext_media_color: { palette: [] },
    original_info: { width, height, focus_rects: [] },
    sizes: { large: size, medium: size, small: size, thumb: size },
  };
}

function SafeImage(props: ImgHTMLAttributes<HTMLImageElement>) {
  const [failed, fail] = useState(false);
  return failed
    ? h(
        "span",
        {
          className: `${props.className || ""} media-unavailable image-unavailable`,
        },
        "Image unavailable",
      )
    : h("img", {
        ...props,
        loading: "lazy",
        referrerPolicy: "no-referrer",
        onError: () => fail(true),
      });
}

const displayName = (user: PostCard["user"]) =>
  user.name && /[\p{L}\p{N}\p{Emoji_Presentation}]/u.test(user.name)
    ? user.name
    : user.username || "Saved post";

function Avatar({ user }: { user: PostCard["user"] }) {
  const [failed, fail] = useState(false);
  return h(
    "span",
    { className: "saved-avatar", "aria-hidden": true },
    !failed && mediaUrl(user.profile_image_url)
      ? h("img", {
          src: mediaUrl(user.profile_image_url),
          alt: "",
          width: 36,
          height: 36,
          referrerPolicy: "no-referrer",
          onError: () => fail(true),
        })
      : Array.from(displayName(user))[0].toUpperCase(),
  );
}

function SavedVideo({ media }: { media: Media }) {
  const [failed, fail] = useState(false);
  const variants = (media.variants || [])
    .filter((v) => v.content_type === "video/mp4" && mediaUrl(v.url))
    .sort((a, b) => (a.bit_rate || 0) - (b.bit_rate || 0));
  const source =
    variants.find((v) => (v.bit_rate || 0) >= 800000) || variants.at(-1);
  const width = media.width && media.width > 0 ? media.width : 1600;
  const height = media.height && media.height > 0 ? media.height : 900;
  return h(
    "div",
    {
      className: "saved-video-frame",
      tabIndex: -1,
      "aria-live": "polite",
      style: { aspectRatio: `${width} / ${height}` },
    },
    failed || !source
      ? h(
          "span",
          { className: "media-unavailable" },
          "Video unavailable. Open on X to try the original.",
        )
      : h("video", {
          className: "saved-video",
          width,
          height,
          controls: true,
          playsInline: true,
          preload: "none",
          poster: mediaUrl(media.preview_image_url),
          src: mediaUrl(source.url),
          onError: (event) => {
            if (document.activeElement === event.currentTarget)
              event.currentTarget.parentElement?.focus({ preventScroll: true });
            fail(true);
          },
          "aria-label": "Saved post video",
        }),
  );
}

function SavedTweet({ card }: { card: PostCard }) {
  const { post, user, media } = card,
    text = post.note_tweet?.text || post.text || "";
  const entities = post.note_tweet?.entities || post.entities || {};
  const locate = (e: Entity, token: string): Entity | undefined => {
    const points = Array.from(text);
    if (points.slice(e.start, e.end).join("") === token) return e;
    const at = text.indexOf(token);
    if (!token || at < 0) return;
    const start = Array.from(text.slice(0, at)).length;
    return { ...e, start, end: start + Array.from(token).length };
  };
  const adapted: Tweet = {
    __typename: "Tweet",
    id_str: post.id,
    text,
    lang: "",
    created_at: post.created_at || "",
    display_text_range: [0, Array.from(text).length],
    favorite_count: 0,
    conversation_count: 0,
    news_action_type: "conversation",
    user: {
      id_str: "",
      name: user.name || user.username || "Saved post",
      screen_name: user.username || "i",
      profile_image_url_https: "",
      profile_image_shape: "Circle",
      verified: false,
      is_blue_verified: false,
    },
    edit_control: {
      edit_tweet_ids: [post.id],
      editable_until_msecs: "",
      is_edit_eligible: false,
      edits_remaining: "0",
    },
    isEdited: false,
    isStaleEdit: false,
    entities: {
      urls: (entities.urls || [])
        .flatMap((e) => {
          const found = locate(e, e.url || "");
          return found && webUrl(e.expanded_url || e.url) ? [found] : [];
        })
        .map((e) => ({
          indices: [e.start, e.end],
          url: e.url || "",
          expanded_url: webUrl(e.expanded_url || e.url),
          display_url: e.display_url || e.expanded_url || e.url || "",
        })),
      user_mentions: (entities.mentions || [])
        .flatMap((e) => {
          const found = locate(e, "@" + e.username);
          return found && /^[A-Za-z0-9_]+$/.test(e.username || "")
            ? [found]
            : [];
        })
        .map((e) => ({
          indices: [e.start, e.end],
          id_str: "",
          name: e.username!,
          screen_name: e.username!,
        })),
      hashtags: (entities.hashtags || [])
        .flatMap((e) => {
          const found = locate(e, "#" + e.tag);
          return found ? [found] : [];
        })
        .map((e) => ({ indices: [e.start, e.end], text: e.tag || "" })),
      symbols: [],
    },
    mediaDetails: media
      .filter((m) => m.type === "photo" && mediaUrl(m.url))
      .map(imageMedia),
  };
  const tweet = enrichTweet(adapted);
  // react-tweet expects HTML-escaped syndication text; X API text is unescaped.
  tweet.entities = tweet.entities.map((e) =>
    e.type === "text" ? { ...e, text: escape(e.text) } : e,
  );
  // An attached media link is already represented by the image/video below.
  // Match the current post exactly so quote and external links stay visible.
  tweet.entities = tweet.entities.filter(
    (e) =>
      !(
        e.type === "url" &&
        media.length &&
        new RegExp(
          `^https://(?:twitter|x)\\.com/[^/]+/status/${post.id}/(?:photo|video)/`,
        ).test(e.href)
      ),
  );
  const last = tweet.entities.at(-1);
  if (last?.type === "text") last.text = last.text.trimEnd();
  const date = post.created_at ? new Date(post.created_at) : null;
  return h(
    TweetContainer,
    { className: "saved-tweet", children: null },
    h(
      "header",
      { className: "saved-author" },
      h(Avatar, { user }),
      h(
        "div",
        { className: "saved-identity" },
        h("strong", null, displayName(user)),
        user.username ? h("span", null, "@" + user.username) : null,
      ),
      date && !isNaN(date.getTime())
        ? h(
            "time",
            { dateTime: post.created_at },
            date.toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
              year: "numeric",
            }),
          )
        : null,
    ),
    h(TweetBody, { tweet }),
    tweet.mediaDetails?.length
      ? h(TweetMedia, { tweet, components: { MediaImg: SafeImage } })
      : null,
    ...media
      .filter((m) => ["video", "animated_gif"].includes(m.type))
      .map((m) => h(SavedVideo, { media: m, key: m.media_key })),
    post.article?.plain_text
      ? h(
          "section",
          { className: "saved-article" },
          h("h3", null, post.article.title),
          h("p", null, post.article.plain_text),
        )
      : null,
  );
}

const roots = new WeakMap<HTMLElement, Root>();
export function renderTweets(
  node: HTMLElement,
  cards: PostCard[],
  openLink: (url: string) => void,
) {
  let root = roots.get(node);
  if (!root) {
    root = createRoot(node);
    roots.set(node, root);
  }
  // The surrounding panel manages focus and scroll synchronously. Commit the
  // React island before it positions the reader or a matched evidence passage.
  flushSync(() =>
    root.render(
      h(
        "div",
        {
          onClick: (e) => {
            if (
              e.defaultPrevented ||
              e.button !== 0 ||
              e.metaKey ||
              e.ctrlKey ||
              e.shiftKey ||
              e.altKey
            )
              return;
            const a = (e.target as HTMLElement).closest("a");
            if (a) {
              e.preventDefault();
              const url = webUrl(a.href);
              if (url) openLink(url);
            }
          },
        },
        ...cards.map((card, i) =>
          h(
            "section",
            {
              key: card.post.id,
              className: i ? "direct-context" : "",
              "aria-label":
                card.relation === "quoted"
                  ? "Quoted post"
                  : card.relation === "replied_to"
                    ? "Replying to"
                    : "Bookmarked post",
            },
            i
              ? h(
                  "h3",
                  null,
                  card.relation === "quoted" ? "Quoted post" : "Replying to",
                )
              : null,
            h(SavedTweet, { card }),
          ),
        ),
      ),
    ),
  );
}

export function clearTweets(node: HTMLElement) {
  const root = roots.get(node);
  if (root) flushSync(() => root.render(null));
}
