import React from 'react';
import { useLinkHandler } from '../lib/router';

interface Props extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
}

/** アプリ内リンク。修飾キー付きクリックはブラウザ既定の動作に任せる。 */
export const Link: React.FC<Props> = ({ href, children, ...rest }) => {
  const onClick = useLinkHandler();
  return (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  );
};
