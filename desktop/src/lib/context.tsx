import { createContext, useContext } from "react";

import type { ManagerApi } from "./api";

export const ApiContext = createContext<ManagerApi | null>(null);

export function useApi(): ManagerApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi must be used inside <ApiContext.Provider>");
  return api;
}
