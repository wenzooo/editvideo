import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

type MenuProps = {
  /** Testo/nodo mostrato sul trigger. */
  label?: ReactNode;
  /** Icona opzionale a sinistra del label. */
  icon?: ReactNode;
  /** Allineamento del popover rispetto al trigger. */
  align?: "left" | "right";
  /** Classi extra sul contenitore. */
  className?: string;
  /** Classi extra sul bottone trigger (es. "ghost", "small"). */
  buttonClassName?: string;
  /** title/tooltip nativo del trigger. */
  title?: string;
  /** Nome accessibile del trigger (obbligatorio quando il label è solo un'icona). */
  ariaLabel?: string;
  /** Nasconde la freccetta (utile per trigger a sola icona). */
  hideCaret?: boolean;
  children: ReactNode;
};

/**
 * Menu a tendina accessibile (HUD ctOS).
 * - chiude su click esterno ed Escape
 * - navigazione con frecce/Home/End tra le voci
 * - aria-haspopup / aria-expanded / role=menu
 * Le voci si costruiscono con <MenuItem> e <MenuSeparator>.
 */
export function Menu({
  label,
  icon,
  align = "left",
  className,
  buttonClassName,
  title,
  ariaLabel,
  hideCaret,
  children,
}: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function onMenuKey(e: ReactKeyboardEvent<HTMLDivElement>) {
    const items = Array.from(
      rootRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"]:not([disabled]):not([aria-disabled="true"])',
      ) ?? [],
    );
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(idx + 1) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    }
  }

  return (
    <div className={`menu${className ? " " + className : ""}`} ref={rootRef}>
      <button
        type="button"
        className={`btn menu-trigger${buttonClassName ? " " + buttonClassName : ""}${open ? " open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={ariaLabel}
        title={title}
        onClick={() => setOpen((v) => !v)}
      >
        {icon && <span className="menu-trigger-icon" aria-hidden>{icon}</span>}
        {label != null && <span className="menu-trigger-label">{label}</span>}
        {!hideCaret && <span className="menu-caret" aria-hidden>▾</span>}
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          className={`menu-pop${align === "right" ? " align-right" : ""}`}
          onKeyDown={onMenuKey}
        >
          <div className="menu-pop-inner" onClick={() => setOpen(false)}>
            {children}
          </div>
        </div>
      )}
    </div>
  );
}

type MenuItemProps = {
  onClick?: () => void;
  /** Se presente rende un <a> (utile per download/risorse). */
  href?: string;
  download?: boolean | string;
  danger?: boolean;
  disabled?: boolean;
  icon?: ReactNode;
  /** Testo secondario a destra (scorciatoia o nota). */
  hint?: ReactNode;
  children: ReactNode;
};

export function MenuItem({
  onClick,
  href,
  download,
  danger,
  disabled,
  icon,
  hint,
  children,
}: MenuItemProps) {
  const cls = `menu-item${danger ? " danger" : ""}`;
  const inner = (
    <>
      <span className="menu-item-icon" aria-hidden>{icon}</span>
      <span className="menu-item-label">{children}</span>
      {hint != null && <span className="menu-item-hint">{hint}</span>}
    </>
  );
  if (href && !disabled) {
    return (
      <a role="menuitem" className={cls} href={href} download={download} tabIndex={-1}>
        {inner}
      </a>
    );
  }
  return (
    <button
      role="menuitem"
      type="button"
      className={cls}
      onClick={onClick}
      disabled={disabled}
      tabIndex={-1}
    >
      {inner}
    </button>
  );
}

export function MenuSeparator() {
  return <div className="menu-sep" role="separator" />;
}
