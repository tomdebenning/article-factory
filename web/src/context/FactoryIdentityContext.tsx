import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";

const DEFAULT_FACTORY_NAME = "The Newsroom";

type FactoryIdentityContextValue = {
  factoryName: string;
  refreshFactoryIdentity: () => void;
};

const FactoryIdentityContext = createContext<FactoryIdentityContextValue>({
  factoryName: DEFAULT_FACTORY_NAME,
  refreshFactoryIdentity: () => undefined,
});

export function FactoryIdentityProvider({ children }: { children: ReactNode }) {
  const [factoryName, setFactoryName] = useState(DEFAULT_FACTORY_NAME);

  const refreshFactoryIdentity = useCallback(() => {
    void api
      .getSettings()
      .then((settings) => {
        const name = settings.gateway_display_name?.trim();
        setFactoryName(name || DEFAULT_FACTORY_NAME);
      })
      .catch(() => {
        /* keep last known name on transient errors */
      });
  }, []);

  useEffect(() => {
    refreshFactoryIdentity();
    const timer = setInterval(refreshFactoryIdentity, 15000);
    return () => clearInterval(timer);
  }, [refreshFactoryIdentity]);

  const value = useMemo(
    () => ({ factoryName, refreshFactoryIdentity }),
    [factoryName, refreshFactoryIdentity],
  );

  return <FactoryIdentityContext.Provider value={value}>{children}</FactoryIdentityContext.Provider>;
}

export function useFactoryIdentity(): FactoryIdentityContextValue {
  return useContext(FactoryIdentityContext);
}
