import { useId } from "react";

/** Two or more exclusive choices as the kit's segmented control: real radios under a named group. */
export function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void;
}) {
  const name = useId();
  return (
    <fieldset className="hw-segmented hw-segmented--sm hw-segmented--accent">
      <legend className="hw-visually-hidden">{label}</legend>
      {options.map(([v, text]) => (
        <label key={v}>
          <input type="radio" className="hw-segmented__input" name={name} checked={value === v} onChange={() => onChange(v)} />
          <span className="hw-segmented__option">{text}</span>
        </label>
      ))}
    </fieldset>
  );
}
