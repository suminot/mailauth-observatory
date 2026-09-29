// ベンダー名の表示。
//
// **実在の社名はそのまま出す。** 大塚商会・クオリティアのような名前は
// 訳さない ── 訳せないし、訳すと検索もできなくなる。日本語が英語版に
// 出ること自体は正しい。
//
// **こちらが付けたラベルだけを訳す。** 秘匿の束ねは実在の会社ではなく
// 集計上の入れ物なので、言語に合わせる。最初これを日本語の表示名のまま
// gold に入れてしまい、英語版の表に「その他（秘匿）」と出た
// （ブラウザ検査が捕まえた）。

/** 秘匿の束ねを表す機械の値。 */
export const SUPPRESSED = "other_suppressed";

/** 2026-09 の gold まではこの表示名が入っている。**古い月も読めるようにする。** */
const LEGACY_SUPPRESSED = "その他（秘匿）";

const LABELS = {
  ja: { [SUPPRESSED]: "その他（秘匿）" },
  en: { [SUPPRESSED]: "Other (suppressed)" },
};

/**
 * 表に出す名前。`lang` は "ja" か "en"。
 *
 * 知らない値はそのまま返す ── **消すより出すほうがまし。**
 */
export function vendorLabel(vendor, lang = "ja") {
  const key = vendor === LEGACY_SUPPRESSED ? SUPPRESSED : vendor;
  return LABELS[lang]?.[key] ?? vendor;
}
