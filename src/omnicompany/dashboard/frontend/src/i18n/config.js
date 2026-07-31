/**
 * i18n Configuration
 *
 * Configures i18next for internationalization support.
 * Features:
 * - Lazy-loading of translation namespaces
 * - Language detection from localStorage
 * - Fallback to English for missing translations
 * - Development mode warnings for missing keys
 *
 * NOTE: 'chat' 命名空间已删 —— 人用聊天迁到收编 chatui(自带独立 i18n), 驾驶舱前端
 * 已无任何 useTranslation('chat')/t('chat.*') 消费, 留着只是白进 bundle。
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
// eslint-disable-next-line import-x/order
import LanguageDetector from 'i18next-browser-languagedetector';

// Import translation resources
import enCommon from './locales/en/common.json';
import enSettings from './locales/en/settings.json';
import enAuth from './locales/en/auth.json';
import enSidebar from './locales/en/sidebar.json';
import enCodeEditor from './locales/en/codeEditor.json';
// eslint-disable-next-line import-x/order
import enTasks from './locales/en/tasks.json';

import koCommon from './locales/ko/common.json';
import koSettings from './locales/ko/settings.json';
import koAuth from './locales/ko/auth.json';
import koSidebar from './locales/ko/sidebar.json';
// eslint-disable-next-line import-x/order
import koCodeEditor from './locales/ko/codeEditor.json';

import zhCommon from './locales/zh-CN/common.json';
import zhSettings from './locales/zh-CN/settings.json';
import zhAuth from './locales/zh-CN/auth.json';
import zhSidebar from './locales/zh-CN/sidebar.json';
// eslint-disable-next-line import-x/order
import zhCodeEditor from './locales/zh-CN/codeEditor.json';

import jaCommon from './locales/ja/common.json';
import jaSettings from './locales/ja/settings.json';
import jaAuth from './locales/ja/auth.json';
import jaSidebar from './locales/ja/sidebar.json';
import jaCodeEditor from './locales/ja/codeEditor.json';
// eslint-disable-next-line import-x/order
import jaTasks from './locales/ja/tasks.json';

import ruCommon from './locales/ru/common.json';
import ruSettings from './locales/ru/settings.json';
import ruAuth from './locales/ru/auth.json';
import ruSidebar from './locales/ru/sidebar.json';
import ruCodeEditor from './locales/ru/codeEditor.json';
// eslint-disable-next-line import-x/order
import ruTasks from './locales/ru/tasks.json';

import deCommon from './locales/de/common.json';
import deSettings from './locales/de/settings.json';
import deAuth from './locales/de/auth.json';
import deSidebar from './locales/de/sidebar.json';
import deCodeEditor from './locales/de/codeEditor.json';
// eslint-disable-next-line import-x/order
import deTasks from './locales/de/tasks.json';

import trCommon from './locales/tr/common.json';
import trSettings from './locales/tr/settings.json';
import trAuth from './locales/tr/auth.json';
import trSidebar from './locales/tr/sidebar.json';
import trCodeEditor from './locales/tr/codeEditor.json';
// eslint-disable-next-line import-x/order
import trTasks from './locales/tr/tasks.json';
import itCommon from './locales/it/common.json';
import itSettings from './locales/it/settings.json';
import itAuth from './locales/it/auth.json';
import itSidebar from './locales/it/sidebar.json';
import itCodeEditor from './locales/it/codeEditor.json';
// eslint-disable-next-line import-x/order
import itTasks from './locales/it/tasks.json';

// Import supported languages configuration
import { languages } from './languages.js';

// Get saved language preference from localStorage
const getSavedLanguage = () => {
  try {
    const saved = localStorage.getItem('userLanguage');
    // Validate that the saved language is supported
    if (saved && languages.some(lang => lang.value === saved)) {
      return saved;
    }
    return 'zh-CN';
  } catch {
    return 'zh-CN';
  }
};

// Initialize i18next
i18n
  .use(LanguageDetector) // Detect user language
  .use(initReactI18next) // Pass i18n instance to react-i18next
  .init({
    // Resources containing all translations
    resources: {
      en: {
        common: enCommon,
        settings: enSettings,
        auth: enAuth,
        sidebar: enSidebar,
        codeEditor: enCodeEditor,
        tasks: enTasks,
      },
      ko: {
        common: koCommon,
        settings: koSettings,
        auth: koAuth,
        sidebar: koSidebar,
        codeEditor: koCodeEditor,
      },
      'zh-CN': {
        common: zhCommon,
        settings: zhSettings,
        auth: zhAuth,
        sidebar: zhSidebar,
        codeEditor: zhCodeEditor,
      },
      ja: {
        common: jaCommon,
        settings: jaSettings,
        auth: jaAuth,
        sidebar: jaSidebar,
        codeEditor: jaCodeEditor,
        tasks: jaTasks,
      },
      ru: {
        common: ruCommon,
        settings: ruSettings,
        auth: ruAuth,
        sidebar: ruSidebar,
        codeEditor: ruCodeEditor,
        tasks: ruTasks,
      },
      de: {
        common: deCommon,
        settings: deSettings,
        auth: deAuth,
        sidebar: deSidebar,
        codeEditor: deCodeEditor,
        tasks: deTasks,
      },
      tr: {
        common: trCommon,
        settings: trSettings,
        auth: trAuth,
        sidebar: trSidebar,
        codeEditor: trCodeEditor,
        tasks: trTasks,
      },
      it: {
        common: itCommon,
        settings: itSettings,
        auth: itAuth,
        sidebar: itSidebar,
        codeEditor: itCodeEditor,
        tasks: itTasks,
      },
    },

    // Default language
    lng: getSavedLanguage(),

    // Fallback language when a translation is missing
    fallbackLng: 'en',

    // Enable debug mode in development (logs missing keys to console)
    debug: false,

    // Namespaces - load only what's needed
    ns: ['common', 'settings', 'auth', 'sidebar', 'codeEditor', 'tasks'],
    defaultNS: 'common',

    // Key separator for nested keys (default: '.')
    keySeparator: '.',

    // Namespace separator (default: ':')
    nsSeparator: ':',

    // Save missing translations (disabled - requires manual review)
    saveMissing: false,

    // Interpolation settings
    interpolation: {
      escapeValue: false, // React already escapes values
    },

    // React-specific settings
    react: {
      useSuspense: true, // Use Suspense for lazy-loading
      bindI18n: 'languageChanged', // Re-render on language change
      bindI18nStore: false, // Don't re-render on resource changes
    },

    // Detection options
    detection: {
      // Order of language detection (local storage first)
      order: ['localStorage'],

      // Keys to look for in localStorage
      lookupLocalStorage: 'userLanguage',

      // Cache user language
      caches: ['localStorage'],
    },
  });

// Save language preference when it changes
i18n.on('languageChanged', (lng) => {
  try {
    localStorage.setItem('userLanguage', lng);
  } catch (error) {
    console.error('Failed to save language preference:', error);
  }
});

export default i18n;
