import { createElement as h } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import * as Select from "@radix-ui/react-select";

type Option = { value: string; label: string };
const chevron = (up = false) =>
  h(
    "svg",
    {
      viewBox: "0 0 20 20",
      width: 16,
      height: 16,
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 1.5,
      "aria-hidden": true,
    },
    h("path", { d: up ? "m5 12 5-5 5 5" : "m5 8 5 5 5-5" }),
  );
const check = h(
  "svg",
  {
    viewBox: "0 0 20 20",
    width: 16,
    height: 16,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    "aria-hidden": true,
  },
  h("path", { d: "m4 10 4 4 8-8" }),
);

export class Choice {
  private root: Root;
  private current = "";
  private unavailable = false;
  private hint = "";
  private expanded = false;
  options: Option[];
  onChange: () => void = () => {};
  constructor(
    private element: HTMLElement,
    options: Option[],
    private placeholder = "Choose…",
  ) {
    this.options = options;
    this.root = createRoot(element);
    this.render();
  }
  get focused() {
    return this.expanded || this.element.contains(document.activeElement);
  }
  get value() {
    return this.current;
  }
  set value(value: string) {
    if (value !== this.current) {
      this.current = value;
      this.render();
    }
  }
  get label() {
    return (
      this.options.find((o) => o.value === this.current)?.label ||
      this.placeholder
    );
  }
  get disabled() {
    return this.unavailable;
  }
  set disabled(value: boolean) {
    if (value !== this.unavailable) {
      this.unavailable = value;
      if (value) this.expanded = false;
      this.render();
    }
  }
  set title(value: string) {
    if (value !== this.hint) {
      this.hint = value;
      this.render();
    }
  }
  setOptions(options: Option[], value: string) {
    if (this.focused) return;
    if (
      JSON.stringify(options) === JSON.stringify(this.options) &&
      value === this.current
    )
      return;
    this.options = options;
    this.current = value;
    this.render();
  }
  close() {
    this.expanded = false;
    this.render();
  }
  focus() {
    // Saving can re-enable the trigger before React commits its DOM update.
    flushSync(() => this.render());
    this.element
      .querySelector<HTMLButtonElement>("button")
      ?.focus({ preventScroll: true });
  }
  private render() {
    const id = this.element.id;
    // Menus inside a modal must stay in its top layer, outside its scrolling body.
    const modal = this.element.closest("dialog");
    const container =
      modal || this.element.closest("[popover]") || document.body;
    const boundary =
      this.element.closest(".dialog-body") ||
      document.getElementById("library-scroll");
    this.root.render(
      h(
        Select.Root,
        {
          value: JSON.stringify(this.current),
          disabled: this.unavailable,
          open: this.expanded,
          onOpenChange: (open: boolean) => {
            this.expanded = open;
            this.render();
          },
          onValueChange: (value: string) => {
            this.current = JSON.parse(value);
            this.render();
            this.onChange();
          },
        },
        h(
          Select.Trigger,
          {
            id: `${id}-trigger`,
            className: "choice-trigger",
            title: this.hint,
            "aria-labelledby": `${id}-label ${id}-value`,
            "aria-describedby":
              this.element.getAttribute("aria-describedby") || undefined,
          },
          h(Select.Value, { id: `${id}-value` }, this.label),
          h(Select.Icon, { className: "choice-chevron" }, chevron()),
        ),
        h(
          Select.Portal,
          { container },
          h(
            Select.Content,
            {
              className: "choice-menu",
              position: "popper",
              sideOffset: 6,
              align: "start",
              collisionPadding: 8,
              collisionBoundary: boundary,
              onEscapeKeyDown: (e: KeyboardEvent) => e.stopPropagation(),
            },
            h(
              Select.ScrollUpButton,
              { className: "choice-scroll" },
              chevron(true),
            ),
            h(
              Select.Viewport,
              { className: "choice-options" },
              this.options.map((o) =>
                h(
                  Select.Item,
                  {
                    key: o.value,
                    value: JSON.stringify(o.value),
                    className: "choice-option",
                    textValue: o.label,
                  },
                  h(Select.ItemText, null, o.label),
                  h(Select.ItemIndicator, { className: "choice-check" }, check),
                ),
              ),
            ),
            h(
              Select.ScrollDownButton,
              { className: "choice-scroll" },
              chevron(),
            ),
          ),
        ),
      ),
    );
  }
}
