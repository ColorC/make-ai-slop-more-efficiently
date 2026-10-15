

import { useTranslation } from 'react-i18next';
import { Languages } from 'lucide-react';

import { languages } from '../../../i18n/languages';
import { BlueprintSelect } from '../../../components/BlueprintSelect';

type LanguageSelectorProps = {
  compact?: boolean;
};

/**
 * Language Selector Component
 *
 * A dropdown component for selecting the application language.
 * Automatically updates the i18n language and persists to localStorage.
 *
 * Props:
 * @param {boolean} compact - If true, uses compact style (default: false)
 */
export default function LanguageSelector({ compact = false }: LanguageSelectorProps) {
  const { i18n, t } = useTranslation('settings');

  // 2026-08-06: 原生 select → 蓝图自绘件, 回调直接收 value(不再从 event.target 取)。
  const handleLanguageChange = (newLanguage: string) => {
    i18n.changeLanguage(newLanguage);
  };
  const options = languages.map((lang) => ({ value: lang.value, label: lang.nativeName }));

  // Compact style for QuickSettingsPanel
  if (compact) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-transparent bg-muted/50 p-3 transition-colors hover:border-border hover:bg-accent">
        <span className="flex items-center gap-2 text-sm text-foreground">
          <Languages className="h-4 w-4 text-muted-foreground" />
          {t('account.language')}
        </span>
        <BlueprintSelect
          value={i18n.language}
          onChange={handleLanguageChange}
          options={options}
          label={t('account.language')}
          aria-label={t('account.language')}
          style={{ width: 100 }}
        />
      </div>
    );
  }

  // Full style for Settings page
  return (
    <div className="flex items-center justify-between px-4 py-3.5">
      <div>
        <div className="text-sm font-medium text-foreground">
          {t('account.languageLabel')}
        </div>
        <div className="mt-0.5 text-sm text-muted-foreground">
          {t('account.languageDescription')}
        </div>
      </div>
      <BlueprintSelect
        value={i18n.language}
        onChange={handleLanguageChange}
        options={options}
        label={t('account.languageLabel')}
        aria-label={t('account.languageLabel')}
        style={{ width: 144 }}
      />
    </div>
  );
}
