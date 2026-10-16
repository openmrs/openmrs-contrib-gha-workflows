import { Code, Table } from 'nextra/components'
import reference from '../generated/reference.json'

/**
 * Renders one table of a reusable workflow's or composite action's interface,
 * read from its YAML by scripts/generate-workflow-reference.mjs. Pages supply
 * their own headings so the tables appear in the table of contents.
 *
 * @param id Reference key: `workflows/<file name without .yml>` or `actions/<directory>`.
 * @param section `inputs`, `secrets` or `outputs`.
 */
export default function WorkflowReference({ id, section }) {
  const entry = reference[id]
  if (!entry) {
    throw new Error(`No workflow reference for "${id}". Is it a reusable workflow or composite action?`)
  }
  const rows = entry[section]
  if (!rows) {
    throw new Error(`Unknown section "${section}"; use inputs, secrets or outputs.`)
  }
  if (rows.length === 0) {
    return <p>None.</p>
  }

  if (section === 'inputs') {
    return (
      <ReferenceTable
        rows={rows}
        columns={['Name', 'Type', 'Default', 'Description']}
        cells={(row) => [
          <Name key="name" row={row} />,
          row.type,
          row.default === undefined || row.default === '' ? '' : <Code>{String(row.default)}</Code>,
          <Description key="description" text={row.description} />,
        ]}
      />
    )
  }
  return (
    <ReferenceTable
      rows={rows}
      columns={['Name', 'Description']}
      cells={(row) => [<Name key="name" row={row} />, <Description key="description" text={row.description} />]}
    />
  )
}

function ReferenceTable({ rows, columns, cells }) {
  return (
    <div className="workflow-reference">
      <Table>
        <thead>
          <Table.Tr>
            {columns.map((column) => (
              <Table.Th key={column}>{column}</Table.Th>
            ))}
          </Table.Tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Table.Tr key={row.name}>
              {cells(row).map((cell, i) => (
                <Table.Td key={i}>{cell}</Table.Td>
              ))}
            </Table.Tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

function Name({ row }) {
  return (
    <>
      <Code>{row.name}</Code>
      {row.required && <em> required</em>}
    </>
  )
}

/** Renders `backtick` spans in YAML descriptions as inline code. */
function Description({ text }) {
  return text.split(/(`[^`]+`)/).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') ? <Code key={i}>{part.slice(1, -1)}</Code> : part,
  )
}
