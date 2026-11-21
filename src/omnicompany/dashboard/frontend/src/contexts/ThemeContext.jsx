import React, { createContext, useContext, useMemo } from 'react';
import { useUiSettings } from '../stores/uiSettingsStore';
import { resolveSkin } from '../skins/skinRegistry';

const ThemeContext = createContext();

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

// 明暗不再单独存一份: 它是当前皮肤的属性(skins/skinRuntime.ts 负责把 class="dark" 落到 <html>)。
// 这里只把皮肤的明暗读给还在用 useTheme() 的组件; 切换明暗 = 换到同套件里成对的那套皮肤。
export const ThemeProvider = ({ children }) => {
  const skinId = useUiSettings((state) => state.skin);
  const setSkin = useUiSettings((state) => state.setSkin);

  const value = useMemo(() => {
    const skin = resolveSkin(skinId);
    return {
      isDarkMode: skin.base === 'dark',
      toggleDarkMode: () => { if (skin.pair) setSkin(skin.pair); },
    };
  }, [skinId, setSkin]);

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};
