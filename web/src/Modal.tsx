import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** One modal contract for navigation, filters and exact owner previews. */
export function Modal({
  children,
  labelledBy,
  className = "",
  onDismiss,
  dismissible = true,
  returnFocus,
}: {
  children: ReactNode;
  labelledBy: string;
  className?: string;
  onDismiss: () => void;
  dismissible?: boolean;
  returnFocus?: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current!;
    const previous =
      returnFocus ?? (document.activeElement as HTMLElement | null);
    const siblings = Array.from(document.body.children).filter(
      (e) => e !== node,
    ) as HTMLElement[];
    const saved = siblings.map((e) => ({
      e,
      inert: e.inert,
      hidden: e.getAttribute("aria-hidden"),
    }));
    saved.forEach(({ e }) => {
      e.inert = true;
      e.setAttribute("aria-hidden", "true");
    });
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (node.showModal) node.showModal();
    else {
      node.setAttribute("open", "");
      node.setAttribute("tabindex", "-1");
    }
    const initial = node.querySelector<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]",
    );
    (initial ?? node).focus();
    return () => {
      if (node.close) node.close();
      saved.forEach(({ e, inert, hidden }) => {
        e.inert = inert;
        if (hidden === null) e.removeAttribute("aria-hidden");
        else e.setAttribute("aria-hidden", hidden);
      });
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return createPortal(
    <dialog
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      className={`research-surface modal-dialog ${className}`}
      onCancel={(e) => {
        e.preventDefault();
        if (dismissible) onDismiss();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          if (dismissible) onDismiss();
        }
        if (e.key !== "Tab") return;
        const controls = Array.from(
          dialog.current!.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
          ),
        );
        if (!controls.length) {
          e.preventDefault();
          dialog.current?.focus();
          return;
        }
        const first = controls[0],
          last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }}
    >
      {children}
    </dialog>,
    document.body,
  );
}

export function useSmallScreen() {
  // Match the shared shell's mobile breakpoint, including live window resizing.
  const media = useRef(window.matchMedia?.("(max-width: 760px)"));
  const [, setVersion] = useState(0);
  useEffect(() => {
    const changed = () => setVersion((n) => n + 1);
    media.current?.addEventListener("change", changed);
    return () => media.current?.removeEventListener("change", changed);
  }, []);
  return media.current?.matches ?? false;
}
