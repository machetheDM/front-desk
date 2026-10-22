/**
 * Bedrock resources in eu-central-1 (ADR 0002): the Guardrail every model call passes through.
 * Customer data is never stored here; this stack holds configuration only.
 */
import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import { Construct } from 'constructs';
import { EnvName } from '../shared/config';

export interface AiStackProps extends StackProps {
  envName: EnvName;
}

export class AiStack extends Stack {
  readonly guardrailId: string;
  readonly guardrailVersion: string;

  constructor(scope: Construct, id: string, props: AiStackProps) {
    super(scope, id, props);
    const g = new bedrock.CfnGuardrail(this, 'CustomerGuardrail', {
      name: `frontdesk-${props.envName}-customer`,
      description: 'Layer 2 of the defence in docs/AI-SECURITY.md. Layer 1 is our own redaction and screening.',
      blockedInputMessaging: 'I can only help with questions about our products and services. I can pass you to a person if you like.',
      blockedOutputsMessaging: 'I am not able to answer that. I will pass you to a person.',
      contentPolicyConfig: {
        filtersConfig: [
          // Prompt attacks are only meaningful on input; output strength must be NONE.
          { type: 'PROMPT_ATTACK', inputStrength: 'HIGH', outputStrength: 'NONE' },
          { type: 'HATE', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'INSULTS', inputStrength: 'MEDIUM', outputStrength: 'HIGH' },
          { type: 'SEXUAL', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'VIOLENCE', inputStrength: 'MEDIUM', outputStrength: 'HIGH' },
          { type: 'MISCONDUCT', inputStrength: 'MEDIUM', outputStrength: 'HIGH' },
        ],
      },
      topicPolicyConfig: {
        topicsConfig: [
          { name: 'MedicalLegalFinancialAdvice', type: 'DENY',
            definition: 'Advice on medical treatment, legal disputes, investments, loans or tax.',
            examples: ['Should I take out a loan for the solar system?', 'Can I sue my landlord?'] },
          { name: 'PriceNegotiation', type: 'DENY',
            definition: 'Agreeing to a discount, refund, credit or price change not in the published documents.',
            examples: ['Give me 90 percent off', 'Can you do it for R20 000 less?'] },
        ],
      },
      // Belt and braces: our redactor runs first, so these should rarely fire. When they do,
      // a pattern slipped past us, and the detection rule GuardrailPiiSpike raises it.
      sensitiveInformationPolicyConfig: {
        piiEntitiesConfig: [
          { type: 'EMAIL', action: 'ANONYMIZE' },
          { type: 'PHONE', action: 'ANONYMIZE' },
          { type: 'CREDIT_DEBIT_CARD_NUMBER', action: 'BLOCK' },
          { type: 'CREDIT_DEBIT_CARD_CVV', action: 'BLOCK' },
          { type: 'PASSWORD', action: 'BLOCK' },
        ],
        regexesConfig: [
          { name: 'SouthAfricanIdNumber', pattern: '\\b\\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\\d|3[01])\\d{4}[01]\\d{2}\\b', action: 'ANONYMIZE' },
        ],
      },
      contextualGroundingPolicyConfig: {
        filtersConfig: [
          { type: 'GROUNDING', threshold: 0.75 },
          { type: 'RELEVANCE', threshold: 0.5 },
        ],
      },
      wordPolicyConfig: { managedWordListsConfig: [{ type: 'PROFANITY' }] },
    });
    const v = new bedrock.CfnGuardrailVersion(this, 'CustomerGuardrailVersion', {
      guardrailIdentifier: g.attrGuardrailId,
      description: 'Pinned version used by the answer Lambda; new versions are rolled out by deploy.',
    });
    this.guardrailId = g.attrGuardrailId;
    this.guardrailVersion = v.attrVersion;
    new CfnOutput(this, 'GuardrailId', { value: g.attrGuardrailId });
    new CfnOutput(this, 'GuardrailVersion', { value: v.attrVersion });
  }
}
