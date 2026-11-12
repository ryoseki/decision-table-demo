import { useEffect, useState } from "react";
import { parseHash, type Route } from "./route";

// 画面の部品から使うフック（Reactに依存する）。ロジック（route.ts）はここを取り込まない。
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
