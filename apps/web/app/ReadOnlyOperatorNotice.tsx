type ReadOnlyOperatorNoticeProps = {
  className?: string;
};

export function ReadOnlyOperatorNotice({ className }: ReadOnlyOperatorNoticeProps) {
  const classes = ['operator-read-only-note', className].filter(Boolean).join(' ');

  return (
    <p className={classes} role="status">
      Operator actions are unavailable in this public read-only demo. Use a protected operator
      deployment to make changes.
    </p>
  );
}
