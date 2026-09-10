import React from 'react';
import { useLinkHandler } from '../lib/router';

interface Props extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
}

/** アプリ内リンク。修飾キー付きクリックはブラウザ既定の動作に任せる。
 *
 *  onClick を渡された場合は**先に呼んでから**ルータの遷移を行う。
 *  以前は `{...rest}` を onClick より後に展開していたため、onClick を
 *  渡すとルータの遷移が黙って上書きされ、リンクがフルリロードになった。
 *  渡した側で preventDefault すれば遷移を止められる（useLinkHandler が
 *  defaultPrevented を見ている）。 */
export const Link: React.FC<Props> = ({ href, children, onClick, ...rest }) => {
  const navigateOnClick = useLinkHandler();
  return (
    <a
      href={href}
      {...rest}
      onClick={(event) => {
        onClick?.(event);
        navigateOnClick(event);
      }}
    >
      {children}
    </a>
  );
};
