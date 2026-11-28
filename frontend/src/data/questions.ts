// 設問A01〜A12。文言・資料と確認する欄・Yes/Noの記入例は docs/requirements.md の
// 「アカウント・APIの不正利用」の表から転記している。
export type QuestionId =
  | "A01" | "A02" | "A03" | "A04" | "A05" | "A06"
  | "A07" | "A08" | "A09" | "A10" | "A11" | "A12";

export type Question = {
  id: QuestionId;
  /** 資料と確認する欄 */
  source: string;
  /** 設問 */
  prompt: string;
  /** Yesの条件と記入例 */
  yes: string;
  /** Noの条件と記入例 */
  no: string;
};

export const questions: readonly Question[] = [
  { id: "A01", source: "Result of the target login in the login history / ログイン履歴の対象ログインの結果", prompt: "Did the login mentioned in the inquiry succeed? / 問い合わせにあったログインは成功したか？", yes: "Succeeded / 成功", no: "Failed / 失敗" },
  { id: "A02", source: "Verified inquiry: answer about the login / 本人確認済みの問い合わせのログインに関する回答", prompt: "Did the user answer that they did not perform the login? / 本人は「自分が行ったログインではない」と回答したか？", yes: "User says they did not log in / 自分はログインしていない", no: "User says they logged in / 自分がログインした" },
  { id: "A03", source: "Number of registered email address changes in the account change history / アカウント変更履歴の登録メールアドレスの変更回数", prompt: "Was the registered email address changed during the target period? / 対象期間に登録メールアドレスが変更されたか？", yes: "One or more (example: 1) / 1回以上（例は1回）", no: "0 / 0回" },
  { id: "A04", source: "Verified inquiry: answer about the email address change / 本人確認済みの問い合わせのメールアドレス変更に関する回答", prompt: "Did the user answer that they did not make the email address change? / 本人は「自分が行ったメールアドレス変更ではない」と回答したか？", yes: "User says they did not change it / 自分は変更していない", no: "User says they changed it / 自分が変更した" },
  { id: "A05", source: "Number of password changes in the account change history / アカウント変更履歴のパスワードの変更回数", prompt: "Was the password changed during the target period? / 対象期間にパスワードが変更されたか？", yes: "One or more (example: 1) / 1回以上（例は1回）", no: "0 / 0回" },
  { id: "A06", source: "Verified inquiry: answer about the password change / 本人確認済みの問い合わせのパスワード変更に関する回答", prompt: "Did the user answer that they did not make the password change? / 本人は「自分が行ったパスワード変更ではない」と回答したか？", yes: "User says they did not change it / 自分は変更していない", no: "User says they changed it / 自分が変更した" },
  { id: "A07", source: "Credits consumed in the target period in the credit usage history / クレジット利用履歴の対象期間の消費量", prompt: "Were credits consumed during the target period? / 対象期間にクレジットが消費されたか？", yes: "More than 0 (example: 120 credits) / 0より多い（例は120クレジット）", no: "0 credits / 0クレジット" },
  { id: "A08", source: "Verified inquiry: answer about the credit usage / 本人確認済みの問い合わせのクレジット消費に関する回答", prompt: "Did the user answer that they do not recognize that usage? / 本人は「その利用に心当たりがない」と回答したか？", yes: "User does not recognize the usage / 心当たりがない", no: "User says it was their usage / 自分の利用である" },
  { id: "A09", source: "Number of new API keys issued in the target period in the API key issuance history / APIキー発行履歴の対象期間の新規発行数", prompt: "Was an API key newly issued during the target period? / 対象期間にAPIキーが新しく発行されたか？", yes: "One or more (example: 1) / 1件以上（例は1件）", no: "0 / 0件" },
  { id: "A10", source: "Verified inquiry: answer about the issuance of the target key / 本人確認済みの問い合わせの対象キーの発行に関する回答", prompt: "Did the user answer that they did not issue that API key? / 本人は「そのAPIキーは自分が発行したものではない」と回答したか？", yes: "User says they did not issue it / 自分は発行していない", no: "User says they issued it / 自分が発行した" },
  { id: "A11", source: "Number of successful calls with the target key in the API usage history / API利用履歴の対象キーによる呼び出しの成功件数", prompt: "Were there successful calls with the target API key? / 対象のAPIキーで成功した呼び出しがあったか？", yes: "One or more (example: 3) / 1件以上（例は3件）", no: "0 / 0件" },
  { id: "A12", source: "Verified inquiry: answer about the target API usage / 本人確認済みの問い合わせの対象のAPI利用に関する回答", prompt: "Did the user answer that they do not recognize that API usage? / 本人は「そのAPI利用に心当たりがない」と回答したか？", yes: "User does not recognize the usage / 心当たりがない", no: "Usage by an app the user set up / 自分が設定したアプリによる利用である" },
];
