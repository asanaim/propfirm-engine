import { useCallback, useEffect, useRef, useState } from "react";
import { Menu as RaMenu, usePermissions } from "react-admin";
import { Terrain } from "./components/Terrain";
import { ICONS, SIZE, STROKE } from "./icons";
import { useTilt } from "./motion";
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

/** The left menu in the pipridge style: items on a plane that leans toward
the pointer, each rising in from depth and lifting when hovered, over the
ridge terrain drifting at the foot (still for reduced motion). */
export function Menu() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  const area = useRef<HTMLDivElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const [webgl, setWebgl] = useState(true);
  const noWebgl = useCallback(() => {
    setWebgl(false);
  }, []);
  useTilt(plane, 3, area);
  useStagger(plane, screens.size);
  return (
    <div className="menu-3d" ref={area}>
      {webgl ? <Terrain area={area} onUnavailable={noWebgl} /> : <div className="menu-ridge-fallback" aria-hidden="true" />}
      <div className="menu-3d-plane" ref={plane}>
        <MenuItems screens={screens} />
      </div>
    </div>
  );
}

function MenuItems({ screens }: { screens: Set<string> }) {
  return (
    <RaMenu>
      <RaMenu.DashboardItem primaryText="My work" leftIcon={<ICONS.work size={SIZE} strokeWidth={STROKE} />} />
      <RaMenu.ResourceItems />
      {screens.has("settings") ? (
        <RaMenu.Item to="/settings" primaryText="System settings" leftIcon={<ICONS.settings size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("news") ? <RaMenu.Item to="/news" primaryText="News desk" leftIcon={<ICONS.news size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("anomalies") ? (
        <RaMenu.Item to="/anomalies" primaryText="Anomaly report" leftIcon={<ICONS.anomalies size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("audit") ? <RaMenu.Item to="/audit" primaryText="Security log" leftIcon={<ICONS.audit size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("methods") ? (
        <RaMenu.Item to="/payment-methods" primaryText="Payment methods" leftIcon={<ICONS.methods size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("site") ? <RaMenu.Item to="/site" primaryText="Website" leftIcon={<ICONS.site size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("policies") ? (
        <RaMenu.Item to="/policies" primaryText="Policy pages" leftIcon={<ICONS.policies size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
    </RaMenu>
  );
}
