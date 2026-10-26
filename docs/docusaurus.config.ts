import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'docassay',
  tagline: 'pytest for document ingestion',
  favicon: 'img/favicon.svg',

  url: 'https://andrewx24.github.io',
  baseUrl: '/Docassay/',
  organizationName: 'Andrewx24',
  projectName: 'Docassay',
  trailingSlash: false,

  onBrokenLinks: 'throw',
  markdown: {
    mermaid: true,
    hooks: {onBrokenMarkdownLinks: 'throw'},
  },
  themes: ['@docusaurus/theme-mermaid'],

  i18n: {defaultLocale: 'en', locales: ['en']},

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/Andrewx24/Docassay/edit/main/docs/',
        },
        blog: false,
        theme: {customCss: './src/css/custom.css'},
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {respectPrefersColorScheme: true},
    navbar: {
      title: 'docassay',
      logo: {alt: 'docassay', src: 'img/favicon.svg'},
      items: [
        {type: 'docSidebar', sidebarId: 'main', position: 'left', label: 'Docs'},
        {to: '/implementation/build-plan', label: 'Build plan', position: 'left'},
        {href: 'https://github.com/Andrewx24/Docassay', label: 'GitHub', position: 'right'},
      ],
    },
    footer: {
      style: 'dark',
      copyright: `docassay · Apache-2.0 · ${new Date().getFullYear()}`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'python', 'yaml', 'json', 'toml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
