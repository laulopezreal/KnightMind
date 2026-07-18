/**
 * The app's UI copy is authored in English, so all dates/times/numbers must
 * format in English too — regardless of the visitor's browser locale. Passing
 * this to every `toLocale*` / `Intl.*` call keeps a Spanish (or any non-English)
 * browser from rendering e.g. "lunes, 13 jul" inside otherwise-English text.
 *
 * If the app is ever fully localized, swap this for the active UI locale.
 */
export const LOCALE = 'en-US';
