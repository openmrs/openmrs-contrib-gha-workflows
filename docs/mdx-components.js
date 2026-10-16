import { useMDXComponents as getDocsMDXComponents } from 'nextra-theme-docs'
import WorkflowReference from './components/workflow-reference'

const docsComponents = getDocsMDXComponents()

export function useMDXComponents(components) {
  return {
    ...docsComponents,
    WorkflowReference,
    ...components,
  }
}
