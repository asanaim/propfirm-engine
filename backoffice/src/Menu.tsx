import { useCallback, useEffect, useRef, useState } from "react";
import { Collapse } from "@mui/material";
import { ChevronDown } from "lucide-react";
import { Menu as RaMenu, usePermissions, useSidebarState } from "react-admin";
import { useLocation } from "react-router-dom";
import { Terrain } from "./components/Terrain";

/** The terrain rests this long after the pointer last moved (it holds its
last frame; a screen left open costs no drawing). */
const REST_MS = 3_000;
import { ICONS, SIZE, STROKE } from "./icons";
import { GROUPS, type GroupKey, PLACES, type Place } from "./places";
import { screensFor } from "./screens";

/** Numbers each menu item (--i) for the staggered entrance. */
function useStagger(root: React.RefObject<HTMLElement | null>, count: number): void {
  useEffect(() => {
    const items = root.current?.querySelectorAll<HTMLElement>(".RaMenuItemLink-root") ?? [];
    items.forEach((item, i) => {
      item.style.setProperty("--i", i.toString());
    });
  }, [root, count]);
}

/** The left menu in the pipridge style: the menu itself stays still; only
the item under the pointer moves, lifting toward the reader with its icon
turning, and items rise in from depth when the page opens. The ridge
terrain drifts at the foot (all still for reduced motion). */
export function Menu() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  const area = useRef<HTMLDivElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const [webgl, setWebgl] = useState(true);
  const noWebgl = useCallback(() => {
    setWebgl(false);
  }, []);
  useStagger(plane, screens.size);
  return (
    <div className="menu-3d" ref={area}>
      {webgl ? <Terrain area={area} onUnavailable={noWebgl} idleMs={REST_MS} /> : <div className="menu-ridge-fallback" aria-hidden="true" />}
      <div className="menu-3d-plane" ref={plane}>
        <MenuItems screens={screens} />
      </div>
    </div>
  );
}

const OPEN_KEY = "pipridge.menu.open";

/** The sections the user opened or closed, kept in this browser only. */
function storedOpen(): Partial<Record<GroupKey, boolean>> {
  try {
    const raw = window.localStorage.getItem(OPEN_KEY);
    return raw ? (JSON.parse(raw) as Partial<Record<GroupKey, boolean>>) : {};
  } catch {
    return {};
  }
}

function keepOpen(state: Partial<Record<GroupKey, boolean>>): void {
  try {
    window.localStorage.setItem(OPEN_KEY, JSON.stringify(state));
  } catch {
    // storage may be off; the menu still works for this visit
  }
}

/** Whether `pathname` is this place or a page below it. */
export function isAt(pathname: string, path: string): boolean {
  return path === "/" ? pathname === "/" : pathname === path || pathname.startsWith(`${path}/`);
}

function Item({ place }: { place: Place }) {
  const Icon = ICONS[place.icon];
  const icon = <Icon size={SIZE} strokeWidth={STROKE} />;
  return place.path === "/" ? (
    <RaMenu.DashboardItem primaryText={place.label} leftIcon={icon} />
  ) : (
    <RaMenu.Item to={place.path} primaryText={place.label} leftIcon={icon} />
  );
}

/** The menu in sections (rule AC28): My work and Notifications at the top,
then Traders, Money, Risk, Team and Settings, each opening and closing.
The section of the open page opens by itself; with the sidebar folded to
icons every item shows, since there is no room for section names. */
export function MenuItems({ screens }: { screens: Set<string> }) {
  const { pathname } = useLocation();
  const [sidebarOpen] = useSidebarState();
  const [open, setOpen] = useState<Partial<Record<GroupKey, boolean>>>(storedOpen);
  const visible = PLACES.filter((p) => p.screen === null || screens.has(p.screen));
  const toggle = (key: GroupKey, now: boolean) => {
    const next = { ...open, [key]: !now };
    setOpen(next);
    keepOpen(next);
  };
  return (
    <RaMenu>
      {visible
        .filter((p) => p.group === null)
        .map((p) => (
          <Item key={p.path} place={p} />
        ))}
      {GROUPS.map((g) => {
        const items = visible.filter((p) => p.group === g.key);
        if (items.length === 0) return null;
        const here = items.some((p) => isAt(pathname, p.path));
        const expanded = !sidebarOpen || here || open[g.key] === true;
        const id = `menu-group-${g.key}`;
        return (
          <div key={g.key} className="menu-group">
            {sidebarOpen ? (
              <button
                type="button"
                className={`menu-group-head${here ? " menu-group-here" : ""}`}
                aria-expanded={expanded}
                aria-controls={id}
                onClick={() => {
                  toggle(g.key, expanded);
                }}
              >
                <span>{g.label}</span>
                <ChevronDown aria-hidden="true" size={16} strokeWidth={STROKE} className="menu-group-chevron" />
              </button>
            ) : null}
            <Collapse in={expanded} id={id} timeout="auto">
              {items.map((p) => (
                <Item key={p.path} place={p} />
              ))}
            </Collapse>
          </div>
        );
      })}
    </RaMenu>
  );
}
