# 提供商完整目录与 Footer 覆盖矩阵

检索：2026-10-03T02:53:25.758109+00:00；目录来源：[models.dev](https://models.dev/api.json)；原始响应 SHA-256：`81ad3b1d6cef0e1c0895d0fe5f493b45114e799b22ac3f1d376d1d5a5e955e96`。

本目录共 **226 个入口**，地区/订阅/代理分列；不是全球 AI 厂商总量。资料来源：[models.dev 官方项目](https://github.com/anomalyco/models.dev)、[Hermes 官方提供商说明](https://hermes-agent.nousresearch.com/docs/integrations/providers/)。

## 覆盖层级

所有下列 ID 均可作为 provider 标签通过 Hermes canonical footer 路径；fixture 对全部目录 ID 运行相同回归。这仅证明统计/展示不依赖硬编码厂商白名单，不代表能绕过 Hermes 的认证与客户端支持，也不代表每个账号/模型都已实测。

SDK 列仅记录目录线索，不据此选择端点协议；实际 transport 由 Hermes 解析。空 usage 显示未知，不请求 provider API 来补齐。**本轮已执行 OpenCode Go + deepseek-v4.1-flash 的真实 Gateway 验收**：成功 stdout 与预期退出 7、独立话题投递、max（请求）和 Footer/账本一致通过，见 [实测记录](assets/reference-v1-real-gateway-checks.json)。这不是 DeepSeek 直连接口或其余 225 个目录入口的逐账号认证；它们仍只有目录/协议/canonical 路径证据。

## 协议矩阵

| 协议 | 输入口径 | 缓存 | 主要适用路径 |
|---|---|---|---|
| Hermes canonical | prompt_tokens，或非缓存输入+读+写 | 可选；正值才有明确证据 | 所有被 Hermes 归一化的提供商 |
| OpenAI Chat | prompt_tokens | cached_tokens / DeepSeek hit 为子集 | OpenAI-compatible、OpenRouter、DeepSeek、OpenCode、SiliconFlow、vLLM/llama.cpp |
| Responses / Codex | input_tokens | input_tokens_details.cached_tokens 为子集 | OpenAI / Codex / 兼容网关 |
| Anthropic Messages | input + cache read + cache creation | 独立输入桶 | Anthropic、兼容代理 |
| Gemini | promptTokenCount | cachedContentTokenCount 为子集 | Gemini / Vertex；输出为 candidates+thoughts |
| Bedrock Converse | inputTokens + cache read + write | 独立输入桶 | AWS Bedrock |
| Ollama 原生 | prompt_eval_count | 未报告则未知 | Ollama /api/chat、/api/generate 最终 usage |
| 未知原生协议 | 不猜测 | 不猜测 | 先由 Hermes adapter 归一化；Card 不发推理请求 |

## 官方字段来源

- [Anthropic 缓存口径](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses)
- [Gemini UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata)
- [Bedrock 缓存输入总量](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- [SiliconFlow Chat](https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions)
- [Ollama API](https://docs.ollama.com/api/chat)

## 226 个入口（完整快照）

| ID | 名称 | SDK 线索 | 模型条目数 | 官方文档（目录提供） |
|---|---|---|---:|---|
| 302ai | 302.AI | @ai-sdk/openai-compatible | 122 | https://doc.302.ai |
| abacus | Abacus | @ai-sdk/openai-compatible | 108 | https://abacus.ai/help/api |
| abliteration-ai | abliteration.ai | @ai-sdk/openai-compatible | 3 | https://docs.abliteration.ai/models |
| above | above.dev | @ai-sdk/openai-compatible | 10 | https://above.dev/docs |
| agentrouter | AgentRouter | @ai-sdk/openai-compatible | 5 | https://agentrouter.org/docs/opencode.html |
| agnes | Agnes AI | @ai-sdk/openai-compatible | 3 | https://agnes-ai.com/doc |
| ai-router | AI-ROUTER | @ai-sdk/openai-compatible | 5 | https://ai-router.dev/openai-compatible-api-gateway/ |
| ai21 | AI21 Labs | @ai-sdk/openai-compatible | 2 | https://docs.ai21.com/docs/jamba-foundation-models |
| aiand | ai& | @ai-sdk/openai-compatible | 13 | https://docs.aiand.com/ |
| aihubmix | AIHubMix | @aihubmix/ai-sdk-provider | 137 | https://docs.aihubmix.com |
| ainetcafe | ainetcafe | @ai-sdk/openai-compatible | 1 | https://ainetcafe.com/k3/guides/ |
| aixy | Aixy | @ai-sdk/openai-compatible | 1 | https://docs.aixy-gateway.com/integrations/overview |
| aki-io | AKI.IO | @ai-sdk/openai-compatible | 7 | https://aki.io/docs/ |
| alibaba | Alibaba | @ai-sdk/openai-compatible | 59 | https://www.alibabacloud.com/help/en/model-studio/models |
| alibaba-cn | Alibaba (China) | @ai-sdk/openai-compatible | 91 | https://www.alibabacloud.com/help/en/model-studio/models |
| alibaba-coding-plan | Alibaba Coding Plan | @ai-sdk/openai-compatible | 10 | https://www.alibabacloud.com/help/en/model-studio/coding-plan |
| alibaba-coding-plan-cn | Alibaba Coding Plan (China) | @ai-sdk/openai-compatible | 10 | https://help.aliyun.com/zh/model-studio/coding-plan |
| alibaba-token-plan | Alibaba Token Plan | @ai-sdk/openai-compatible | 28 | https://www.alibabacloud.com/help/en/model-studio/token-plan-overview |
| alibaba-token-plan-cn | Alibaba Token Plan (China) | @ai-sdk/openai-compatible | 28 | https://www.alibabacloud.com/help/zh/model-studio/token-plan-overview |
| amazon-bedrock | Amazon Bedrock | @ai-sdk/amazon-bedrock | 189 | https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html |
| ambient | Ambient | @ai-sdk/openai-compatible | 12 | https://ambient.xyz |
| amd | AMD | @ai-sdk/openai-compatible | 6 | https://developer.amd.com.cn/radeon/tokenfactory |
| anthropic | Anthropic | @ai-sdk/anthropic | 16 | https://docs.anthropic.com/en/docs/about-claude/models |
| anyapi | AnyAPI | @ai-sdk/openai-compatible | 30 | https://docs.anyapi.ai |
| arcee | Arcee | @ai-sdk/openai-compatible | 7 | https://docs.arcee.ai |
| atomic-chat | Atomic Chat | @ai-sdk/openai-compatible | 5 | https://atomic.chat |
| auriko | Auriko | @ai-sdk/openai-compatible | 15 | https://docs.auriko.ai |
| azure | Azure | @ai-sdk/azure | 94 | https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models |
| azure-cognitive-services | Azure Cognitive Services | @ai-sdk/azure | 86 | https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models |
| bailing | Bailing | @ai-sdk/openai-compatible | 2 | https://alipaytbox.yuque.com/sxs0ba/ling/intro |
| baseten | Baseten | @ai-sdk/openai-compatible | 24 | https://docs.baseten.co/inference/model-apis/overview |
| bee | Bee by HEOSSI | @ai-sdk/openai-compatible | 6 | https://bee.heossi.com/docs/sdks |
| berget | Berget.AI | @ai-sdk/openai-compatible | 6 | https://api.berget.ai |
| blueclaw | Blue Claw | @ai-sdk/openai-compatible | 2 | https://blueclaw.network |
| bothub | Bothub | @ai-sdk/openai-compatible | 8 | https://bothub.ru/models |
| cerebras | Cerebras | @ai-sdk/cerebras | 2 | https://inference-docs.cerebras.ai/models/overview |
| chutes | Chutes | @ai-sdk/openai-compatible | 14 | https://llm.chutes.ai/v1/models |
| clarifai | Clarifai | @ai-sdk/openai-compatible | 12 | https://docs.clarifai.com/compute/inference/ |
| claudinio | Claudinio | @ai-sdk/openai-compatible | 2 | https://claudin.io |
| cline-pass | ClinePass | @ai-sdk/openai-compatible | 18 | https://docs.cline.bot/getting-started/clinepass |
| cloudferro-sherlock | CloudFerro Sherlock | @ai-sdk/openai-compatible | 5 | https://docs.sherlock.cloudferro.com/ |
| cloudflare-ai-gateway | Cloudflare AI Gateway | ai-gateway-provider | 50 | https://developers.cloudflare.com/ai-gateway/ |
| cloudflare-workers-ai | Cloudflare Workers AI | @ai-sdk/openai-compatible | 27 | https://developers.cloudflare.com/workers-ai/models/ |
| cohere | Cohere | @ai-sdk/cohere | 17 | https://docs.cohere.com/docs/models |
| coralbricks | CoralBricks | @ai-sdk/openai-compatible | 3 | https://www.coralbricks.ai/docs |
| cortecs | Cortecs | @ai-sdk/openai-compatible | 109 | https://api.cortecs.ai/v1/models |
| crof | CrofAI | @ai-sdk/openai-compatible | 24 | https://crof.ai/docs |
| crossmodel | CrossModel | @ai-sdk/openai-compatible | 68 | https://www.crossmodel.ai/docs |
| crusoe | Crusoe | @ai-sdk/openai-compatible | 11 | https://docs.crusoecloud.com/managed-inference/overview |
| daoxe | DaoXE | @ai-sdk/openai-compatible | 9 | https://daoxe.com/pricing |
| databricks | Databricks | @ai-sdk/openai-compatible | 30 | https://docs.databricks.com/aws/en/machine-learning/foundation-models/ |
| deepinfra | Deep Infra | @ai-sdk/deepinfra | 71 | https://deepinfra.com/models |
| deepseek | DeepSeek | @ai-sdk/openai-compatible | 4 | https://api-docs.deepseek.com/quick_start/pricing |
| digitalocean | DigitalOcean | @ai-sdk/openai-compatible | 101 | https://docs.digitalocean.com/products/gradient-ai-platform/details/models/ |
| dinference | DInference | @ai-sdk/openai-compatible | 6 | https://dinference.com |
| drun | D.Run (China) | @ai-sdk/openai-compatible | 3 | https://www.d.run |
| ebcloud | EBCloud | @ai-sdk/openai-compatible | 4 | https://docs.ebtech.com/ai/model-api.html |
| echo | Echo | @ai-sdk/openai-compatible | 1 | https://echo.tracerml.ai/docs/api |
| edenai | Eden AI | @ai-sdk/openai-compatible | 287 | https://docs.edenai.co |
| empiriolabs | EmpirioLabs AI | @ai-sdk/openai-compatible | 66 | https://docs.empiriolabs.ai |
| engy | engy | @ai-sdk/openai-compatible | 8 | https://engy.ai/pricing |
| evroc | evroc | @ai-sdk/openai-compatible | 16 | https://docs.evroc.com/products/think/overview.html |
| fastrouter | FastRouter | @ai-sdk/openai-compatible | 47 | https://fastrouter.ai/models |
| fireworks-ai | Fireworks AI | @ai-sdk/openai-compatible | 22 | https://fireworks.ai/docs/ |
| freemodel | FreeModel | @ai-sdk/anthropic | 10 | https://freemodel.dev |
| friendli | Friendli | @ai-sdk/openai-compatible | 7 | https://friendli.ai/docs/guides/serverless_endpoints/introduction |
| frogbot | FrogBot | @ai-sdk/openai-compatible | 26 | https://docs.frogbot.ai |
| github-copilot | GitHub Copilot | @ai-sdk/openai-compatible | 34 | https://docs.github.com/en/copilot |
| gitlab | GitLab Duo | gitlab-ai-provider | 30 | https://docs.gitlab.com/user/duo_agent_platform/ |
| gmicloud | GMI Cloud | @ai-sdk/openai-compatible | 17 | https://docs.gmicloud.ai/inference-engine/api-reference/llm-api-reference |
| google | Google | @ai-sdk/google | 39 | https://ai.google.dev/gemini-api/docs/models |
| google-vertex | Vertex | @ai-sdk/google-vertex | 54 | https://cloud.google.com/vertex-ai/generative-ai/docs/models |
| google-vertex-anthropic | Vertex (Anthropic) | @ai-sdk/google-vertex/anthropic | 16 | https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/claude |
| greenpt | GreenPT | @ai-sdk/openai-compatible | 40 | https://docs.greenpt.ai |
| groq | Groq | @ai-sdk/groq | 16 | https://console.groq.com/docs/models |
| helicone | Helicone | @ai-sdk/openai-compatible | 90 | https://helicone.ai/models |
| hetzner | Hetzner | @ai-sdk/openai-compatible | 2 | https://experiments.hetzner.com/docs/inference |
| hpc-ai | HPC-AI | @ai-sdk/openai-compatible | 9 | https://www.hpc-ai.com/doc/docs/quickstart/ |
| huggingface | Hugging Face | @ai-sdk/openai-compatible | 78 | https://huggingface.co/docs/inference-providers |
| hyper | Charm Hyper | @ai-sdk/openai-compatible | 23 | https://hyper.charm.land |
| iflowcn | iFlow | @ai-sdk/openai-compatible | 14 | https://platform.iflow.cn/en/docs |
| impossibl | Impossibl | @ai-sdk/openai-compatible | 76 | https://impossibl.com/docs/models |
| inception | Inception | @ai-sdk/openai-compatible | 3 | https://docs.inceptionlabs.ai/get-started/models |
| inceptron | Inceptron | @ai-sdk/openai-compatible | 4 | https://docs.inceptron.io |
| inco | Inco | @ai-sdk/openai-compatible | 7 | https://platform.inco.ai/docs |
| infer | Infer by Flow7 | @ai-sdk/openai | 2 | https://infer.flow7.org/opencode |
| inference | Inference | @ai-sdk/openai-compatible | 9 | https://inference.net/models |
| inferx | InferX | @ai-sdk/openai-compatible | 12 | https://model.inferx.net/endpoints |
| infomaniak | Infomaniak | @ai-sdk/openai-compatible | 10 | https://www.infomaniak.com/en/hosting/ai-services/open-source-models |
| io-net | IO.NET | @ai-sdk/openai-compatible | 17 | https://io.net/docs/guides/intelligence/io-intelligence |
| iteracompute | IteraCompute | @ai-sdk/openai-compatible | 9 | https://iteracompute.com/docs.html |
| jalapeno | Jalapeno Cloud | @ai-sdk/openai-compatible | 17 | https://www.jalapeno-cloud.ai/docs/ |
| jiekou | Jiekou.AI | @ai-sdk/openai-compatible | 61 | https://docs.jiekou.ai/docs/support/quickstart?utm_source=github_models.dev |
| kenari | Kenari | @ai-sdk/openai-compatible | 60 | https://kenari.id/docs |
| kilo | Kilo Gateway | @ai-sdk/openai-compatible | 397 | https://kilo.ai |
| kimi-code-plan-cn | Kimi For Coding (kimi.com) | @ai-sdk/openai-compatible | 4 | https://www.kimi.com/code/docs/en/kimi-code/models.html |
| kimi-code-plan-global | Kimi For Coding (kimi.ai) | @ai-sdk/openai-compatible | 4 | https://www.kimi.ai/code/docs/en/kimi-code/models.html |
| klokintegration | klokintegration.se | @ai-sdk/openai-compatible | 3 | https://klokintegration.se/docs/ai-api |
| kosmik | Kosmik Compute | @ai-sdk/openai-compatible | 1 | https://api.koscompute.com/docs/ |
| kuae-cloud-coding-plan | KUAE Cloud Coding Plan | @ai-sdk/openai-compatible | 1 | https://docs.mthreads.com/kuaecloud/kuaecloud-doc-online/coding_plan/ |
| lilac | Lilac | @ai-sdk/openai-compatible | 4 | https://docs.getlilac.com/inference/models |
| llama | Llama | @ai-sdk/openai-compatible | 7 | https://llama.developer.meta.com/docs/models |
| llmgateway | DevPass (LLM Gateway) | @ai-sdk/openai-compatible | 214 | https://llmgateway.io/docs |
| llmgateway-providers | LLM Gateway | @ai-sdk/openai-compatible | 438 | https://llmgateway.io/docs |
| llmtech | LLM Tech | @ai-sdk/openai-compatible | 1 | https://llmtech.eu/models/qwen3.8-27b |
| llmtr | LLMTR | @ai-sdk/openai-compatible | 32 | https://llmtr.com/docs |
| lmstudio | LMStudio | @ai-sdk/openai-compatible | 3 | https://lmstudio.ai/models |
| longcat | LongCat | @ai-sdk/openai-compatible | 1 | https://longcat.chat/platform/docs/ |
| lucidquery | LucidQuery | @ai-sdk/openai-compatible | 4 | https://lucidquery.com/docs |
| lynkr | Lynkr | @ai-sdk/openai-compatible | 1 | https://github.com/Fast-Editor/Lynkr |
| meganova | Meganova | @ai-sdk/openai-compatible | 19 | https://docs.meganova.ai |
| melious | Melious | @ai-sdk/openai-compatible | 12 | https://melious.ai/docs/reference/models |
| merge-gateway | Merge Gateway | merge-gateway-ai-sdk-provider | 195 | https://docs.merge.dev/merge-gateway |
| meta | Meta | @ai-sdk/openai | 5 | https://dev.meta.ai/docs |
| minimax | MiniMax (minimax.io) | @ai-sdk/anthropic | 7 | https://platform.minimax.io/docs/guides/quickstart |
| minimax-cn | MiniMax (minimax.cn) | @ai-sdk/anthropic | 7 | https://platform.minimaxi.com/docs/guides/quickstart |
| minimax-cn-coding-plan | MiniMax Token Plan (minimax.cn) | @ai-sdk/anthropic | 8 | https://platform.minimaxi.com/docs/token-plan/intro |
| minimax-coding-plan | MiniMax Token Plan (minimax.io) | @ai-sdk/anthropic | 8 | https://platform.minimax.io/docs/token-plan/intro |
| mistral | Mistral | @ai-sdk/mistral | 34 | https://docs.mistral.ai/getting-started/models/ |
| mixlayer | Mixlayer | @ai-sdk/openai-compatible | 5 | https://docs.mixlayer.com |
| moark | Moark | @ai-sdk/openai-compatible | 2 | https://moark.com/docs/openapi/v1#tag/%E6%96%87%E6%9C%AC%E7%94%9F%E6%88%90 |
| modal | Modal | @ai-sdk/openai-compatible | 4 | https://modal.com/docs/guide/endpoints |
| model-oracle-ai | Model Oracle AI | @ai-sdk/openai-compatible | 15 | https://modeloracle.com/setup/ |
| modelis | Modelis | @ai-sdk/openai-compatible | 9 | https://modelishub.com/pricing |
| modelscope | ModelScope | @ai-sdk/openai-compatible | 7 | https://modelscope.cn/docs/model-service/API-Inference/intro |
| moonshotai | Moonshot AI | @ai-sdk/openai-compatible | 4 | https://platform.moonshot.ai/docs/api/chat |
| moonshotai-cn | Moonshot AI (China) | @ai-sdk/openai-compatible | 4 | https://platform.moonshot.cn/docs/api/chat |
| morph | Morph | @ai-sdk/openai-compatible | 3 | https://docs.morphllm.com/api-reference/introduction |
| nan | NaN | @ai-sdk/openai-compatible | 8 | https://nan.builders/docs/models |
| nano-gpt | NanoGPT | @ai-sdk/openai-compatible | 600 | https://docs.nano-gpt.com |
| nearai | NEAR AI Cloud | @ai-sdk/openai-compatible | 32 | https://docs.near.ai/ |
| nebius | Nebius Token Factory | @ai-sdk/openai-compatible | 21 | https://docs.tokenfactory.nebius.com/ |
| neon | Neon | @ai-sdk/openai-compatible | 46 | https://neon.com/docs |
| neosmith | NeoSmith | @ai-sdk/openai | 4 | https://neosmith.ai/docs |
| neuralwatt | Neuralwatt | @ai-sdk/openai-compatible | 29 | https://portal.neuralwatt.com/docs |
| nova | Nova | @ai-sdk/openai-compatible | 2 | https://nova.amazon.com/dev/documentation |
| novita-ai | NovitaAI | @ai-sdk/openai-compatible | 107 | https://novita.ai/docs/guides/introduction |
| nvidia | Nvidia | @ai-sdk/openai-compatible | 106 | https://docs.api.nvidia.com/nim/ |
| oci | OCI Generative AI | @ai-sdk/openai-compatible | 9 | https://docs.oracle.com/en-us/iaas/Content/generative-ai/pretrained-models.htm |
| ofox | Ofox | @ai-sdk/openai-compatible | 151 | https://ofox.ai/docs |
| ollama-cloud | Ollama Cloud | @ai-sdk/openai-compatible | 24 | https://docs.ollama.com/cloud |
| openai | OpenAI | @ai-sdk/openai | 53 | https://platform.openai.com/docs/models |
| opencode | OpenCode Zen | @ai-sdk/openai-compatible | 116 | https://opencode.ai/docs/zen |
| opencode-go | OpenCode Go | @ai-sdk/openai-compatible | 33 | https://opencode.ai/docs/go |
| openreason | OpenReason | @ai-sdk/openai-compatible | 3 | https://openreason.app/docs |
| openrouter | OpenRouter | @openrouter/ai-sdk-provider | 390 | https://openrouter.ai/models |
| opper | Opper | @ai-sdk/openai-compatible | 57 | https://opper.ai/models |
| orcarouter | OrcaRouter | @ai-sdk/openai-compatible | 117 | https://docs.orcarouter.ai |
| ovhcloud | OVHcloud AI Endpoints | @ai-sdk/openai-compatible | 14 | https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog// |
| pareto | Pareto Inference | @ai-sdk/openai-compatible | 1 | https://docs.paretoinference.com/ |
| pendra | Pendra | @ai-sdk/openai-compatible | 6 | https://pendra.ai/docs/integrations/opencode |
| perplexity | Perplexity | @ai-sdk/perplexity | 4 | https://docs.perplexity.ai |
| perplexity-agent | Perplexity Agent | @ai-sdk/openai | 22 | https://docs.perplexity.ai/docs/agent-api/models |
| pioneer | Pioneer | @ai-sdk/openai-compatible | 116 | https://agent.pioneer.ai/llms.txt |
| poe | Poe | @ai-sdk/openai-compatible | 137 | https://creator.poe.com/docs/external-applications/openai-compatible-api |
| poolside | Poolside | @ai-sdk/openai-compatible | 3 | https://platform.poolside.ai |
| privatemode-ai | Privatemode AI | @ai-sdk/openai-compatible | 11 | https://docs.privatemode.ai/api/overview |
| qihang-ai | QiHang | @ai-sdk/openai-compatible | 9 | https://www.qhaigc.net/docs |
| qiniu-ai | Qiniu | @ai-sdk/openai-compatible | 91 | https://developer.qiniu.com/aitokenapi |
| qvac | QVAC | @qvac/ai-sdk-provider | 9 | https://www.npmjs.com/package/@qvac/ai-sdk-provider |
| regolo-ai | Regolo AI | @ai-sdk/openai-compatible | 18 | https://docs.regolo.ai/ |
| requesty | Requesty | @ai-sdk/openai-compatible | 166 | https://requesty.ai/solution/llm-routing/models |
| routing-run | routing.run | @ai-sdk/openai-compatible | 15 | https://docs.routing.run/api-reference/models |
| runinfra | RunInfra | @ai-sdk/openai-compatible | 7 | https://runinfra.ai/docs |
| sakana | Sakana AI | @ai-sdk/openai-compatible | 4 | https://console.sakana.ai/models |
| salad-cloud | SaladCloud AI Gateway | @saladtechnologies-oss/ai-sdk-provider | 1 | https://docs.salad.com/ai-gateway/explanation/overview |
| sap-ai-core | SAP AI Core | @jerome-benoit/sap-ai-provider-v2 | 50 | https://help.sap.com/docs/sap-ai-core |
| sarvam | Sarvam AI | @ai-sdk/openai-compatible | 2 | https://docs.sarvam.ai/api-reference-docs/getting-started/models |
| scaleway | Scaleway | @ai-sdk/openai-compatible | 16 | https://www.scaleway.com/en/docs/generative-apis/ |
| scnet-token-plan | SCNet Token Plan | @ai-sdk/openai-compatible | 19 | https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/plans/token-plan.html |
| scx-ai | SCX.ai | @ai-sdk/openai-compatible | 4 | https://platform.scx.ai/docs |
| sensenova | SenseNova (China) | @ai-sdk/openai-compatible | 5 | https://platform.sensenova.cn/docs |
| siliconflow | SiliconFlow | @ai-sdk/openai-compatible | 57 | https://cloud.siliconflow.com/models |
| siliconflow-cn | SiliconFlow (China) | @ai-sdk/openai-compatible | 44 | https://cloud.siliconflow.com/models |
| snowflake-cortex | Snowflake Cortex | @ai-sdk/openai-compatible | 25 | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api |
| stackit | STACKIT | @ai-sdk/openai-compatible | 8 | https://docs.stackit.cloud/products/data-and-ai/ai-model-serving/basics/available-shared-models |
| standardcompute | Standard Compute | @openrouter/ai-sdk-provider | 1 | https://standardcompute.com/models |
| stepfun | StepFun (China) | @ai-sdk/openai-compatible | 9 | https://platform.stepfun.com/docs/zh/overview/concept |
| stepfun-ai | StepFun (Global) | @ai-sdk/openai-compatible | 9 | https://platform.stepfun.ai/docs/en/overview/concept |
| stepfun-ai-step-plan | StepFun Step Plan (Global) | @ai-sdk/openai-compatible | 4 | https://platform.stepfun.ai/docs/en/step-plan/integrations/reasoning-api |
| stepfun-step-plan | StepFun Step Plan (China) | @ai-sdk/openai-compatible | 5 | https://platform.stepfun.com/docs/zh/step-plan/integrations/reasoning-api |
| subconscious | Subconscious | @ai-sdk/anthropic | 2 | https://docs.subconscious.dev |
| submodel | submodel | @ai-sdk/openai-compatible | 9 | https://submodel.gitbook.io |
| synthetic | Synthetic | @ai-sdk/openai-compatible | 11 | https://synthetic.new/pricing |
| tempr | Tempr Gateway | @ai-sdk/openai-compatible | 82 | https://temprhq.io/docs/gateway-reference.html |
| tencent-coding-plan | Tencent Coding Plan (China) | @ai-sdk/openai-compatible | 8 | https://cloud.tencent.com/document/product/1772/128947 |
| tencent-token-plan | Tencent Token Plan | @ai-sdk/openai-compatible | 2 | https://cloud.tencent.com/document/product/1823/130060 |
| tencent-tokenhub | Tencent TokenHub | @ai-sdk/openai-compatible | 3 | https://cloud.tencent.com/document/product/1823/130050 |
| tensorx | TensorX | @ai-sdk/openai-compatible | 25 | https://docs.tensorx.ai/ |
| the-grid-ai | The Grid AI | @ai-sdk/openai-compatible | 9 | https://thegrid.ai/docs |
| thinkingmachines | Thinking Machines | @ai-sdk/anthropic | 2 | https://tinker-docs.thinkingmachines.ai/tinker/compatible-apis/anthropic/ |
| tinfoil | Tinfoil | @ai-sdk/openai-compatible | 9 | https://docs.tinfoil.sh |
| togetherai | Together AI | @ai-sdk/togetherai | 29 | https://docs.together.ai/docs/serverless-models |
| tokengo | TokenGo | @ai-sdk/openai-compatible | 13 | https://www.tokengo.com/docs |
| tokenrouter | TokenRouter | @ai-sdk/openai-compatible | 1 | https://www.tokenrouter.com/docs/tokenrouter-feature-guide/ |
| trustedrouter | TrustedRouter | @ai-sdk/openai-compatible | 7 | https://trustedrouter.com/docs |
| umans-ai | Umans AI | @ai-sdk/openai-compatible | 6 | https://app.umans.ai/offers/code/docs/orgs |
| umans-ai-coding-plan | Umans AI Coding Plan | @ai-sdk/openai-compatible | 7 | https://app.umans.ai/offers/code/docs |
| unorouter | UnoRouter | @ai-sdk/openai-compatible | 23 | https://unorouter.com/models |
| upstage | Upstage | @ai-sdk/openai-compatible | 4 | https://developers.upstage.ai/docs/apis/chat |
| v0 | v0 | @ai-sdk/vercel | 3 | https://sdk.vercel.ai/providers/ai-sdk-providers/vercel |
| vancine | Vancine | @ai-sdk/openai-compatible | 8 | https://vancine.com/docs |
| venice | Venice AI | venice-ai-sdk-provider | 116 | https://docs.venice.ai |
| vercel | Vercel AI Gateway | @ai-sdk/gateway | 402 | https://github.com/vercel/ai/tree/5eb85cc45a259553501f535b8ac79a77d0e79223/packages/gateway |
| vispark | Vispark | @ai-sdk/openai-compatible | 3 | https://lab.vispark.in/#vision |
| vivgrid | Vivgrid | @ai-sdk/openai | 36 | https://docs.vivgrid.com/models |
| volcengine | Volcengine Ark | @ai-sdk/openai-compatible | 16 | https://www.volcengine.com/docs/82379/1330310 |
| volcengine-coding-plan | Volcengine Ark Coding Plan | @ai-sdk/openai-compatible | 10 | https://www.volcengine.com/docs/82379/1928261 |
| vultr | Vultr | @ai-sdk/openai-compatible | 15 | https://api.vultrinference.com/ |
| wafer.ai | Wafer | @ai-sdk/openai-compatible | 5 | https://docs.wafer.ai/wafer-pass |
| wallaby | Wallaby | @ai-sdk/openai-compatible | 1 | https://wallabytoken.com/docs |
| wandb | CoreWeave | @ai-sdk/openai-compatible | 29 | https://docs.wandb.ai/inference |
| watsonx | watsonx.ai | watsonx-ai-provider | 5 | https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-supported-foundation-models |
| xai | xAI | @ai-sdk/xai | 13 | https://docs.x.ai/docs/models |
| xiaomi | Xiaomi | @ai-sdk/openai-compatible | 9 | https://platform.xiaomimimo.com/#/docs |
| xiaomi-token-plan-ams | Xiaomi Token Plan (Europe) | @ai-sdk/openai-compatible | 9 | https://platform.xiaomimimo.com/#/docs |
| xiaomi-token-plan-cn | Xiaomi Token Plan (China) | @ai-sdk/openai-compatible | 9 | https://platform.xiaomimimo.com/#/docs |
| xiaomi-token-plan-sgp | Xiaomi Token Plan (Singapore) | @ai-sdk/openai-compatible | 9 | https://platform.xiaomimimo.com/#/docs |
| xpersona | Xpersona | @ai-sdk/openai-compatible | 13 | https://www.xpersona.co/docs |
| zai | Z.AI | @ai-sdk/openai-compatible | 18 | https://docs.z.ai/guides/overview/pricing |
| zai-coding-plan | Z.AI Coding Plan | @ai-sdk/openai-compatible | 7 | https://docs.z.ai/devpack/overview |
| zeldoc | Zeldoc | @ai-sdk/openai-compatible | 1 | https://docs.zeldoc.ai |
| zenifra | Zenifra | @ai-sdk/openai-compatible | 1 | https://docs.zenifra.com |
| zenmux | ZenMux | @ai-sdk/openai-compatible | 129 | https://docs.zenmux.ai |
| zhipuai | Zhipu AI | @ai-sdk/openai-compatible | 17 | https://docs.z.ai/guides/overview/pricing |
| zhipuai-coding-plan | Zhipu AI Coding Plan | @ai-sdk/openai-compatible | 4 | https://docs.bigmodel.cn/cn/coding-plan/overview |

## 本地 Hermes 静态配置快照

静态提取 45 个配置；不执行插件/读取认证。动态注册及用户覆盖不在此静态数量内。

| provider | 别名 | 声明的 api_mode | 源文件 |
|---|---|---|---|
| actual | actual-computer, actualcomputer, aci | chat_completions | plugins/model-providers/actual/__init__.py |
| ai-gateway | vercel, vercel-ai-gateway, ai_gateway, aigateway | 默认/运行时解析 | plugins/model-providers/ai-gateway/__init__.py |
| alibaba | dashscope, alibaba-cloud, qwen-dashscope, aliyun | 默认/运行时解析 | plugins/model-providers/alibaba/__init__.py |
| alibaba-cn | dashscope-cn, alibaba-cloud-cn | 默认/运行时解析 | plugins/model-providers/alibaba/__init__.py |
| alibaba-token-plan | dashscope-token-plan | 默认/运行时解析 | plugins/model-providers/alibaba/__init__.py |
| alibaba-token-plan-cn | dashscope-token-plan-cn | 默认/运行时解析 | plugins/model-providers/alibaba/__init__.py |
| alibaba-coding-plan | alibaba_coding, alibaba-coding, dashscope-coding | 默认/运行时解析 | plugins/model-providers/alibaba-coding-plan/__init__.py |
| alibaba-coding-plan-cn | alibaba-coding-cn, dashscope-coding-cn | 默认/运行时解析 | plugins/model-providers/alibaba-coding-plan/__init__.py |
| anthropic | claude, claude-oauth, claude-code | anthropic_messages | plugins/model-providers/anthropic/__init__.py |
| arcee | arcee-ai, arceeai | 默认/运行时解析 | plugins/model-providers/arcee/__init__.py |
| azure-foundry | azure, azure-ai-foundry, azure-ai | 默认/运行时解析 | plugins/model-providers/azure-foundry/__init__.py |
| bedrock | aws, aws-bedrock, amazon-bedrock, amazon | bedrock_converse | plugins/model-providers/bedrock/__init__.py |
| commandcode | commandcode-chat | chat_completions | plugins/model-providers/commandcode/__init__.py |
| commandcode-anthropic | commandcode-claude | anthropic_messages | plugins/model-providers/commandcode/__init__.py |
| copilot | github-copilot, github-models, github-model, github | 默认/运行时解析 | plugins/model-providers/copilot/__init__.py |
| copilot-acp | github-copilot-acp, copilot-acp-agent | chat_completions | plugins/model-providers/copilot-acp/__init__.py |
| custom | ollama, local, vllm, llamacpp, llama.cpp, llama-cpp | 默认/运行时解析 | plugins/model-providers/custom/__init__.py |
| deepinfra | deep-infra, deepinfra-ai | 默认/运行时解析 | plugins/model-providers/deepinfra/__init__.py |
| deepseek | deepseek-chat, deep-seek | 默认/运行时解析 | plugins/model-providers/deepseek/__init__.py |
| fireworks | fireworks-ai, fw | 默认/运行时解析 | plugins/model-providers/fireworks/__init__.py |
| gemini | google, google-gemini, google-ai-studio | chat_completions | plugins/model-providers/gemini/__init__.py |
| gmi | gmi-cloud, gmicloud | 默认/运行时解析 | plugins/model-providers/gmi/__init__.py |
| huggingface | hf, hugging-face, huggingface-hub | 默认/运行时解析 | plugins/model-providers/huggingface/__init__.py |
| kilocode | kilo-code, kilo, kilo-gateway | 默认/运行时解析 | plugins/model-providers/kilocode/__init__.py |
| meta-ai | meta, muse, muse-spark, model-api, msl | codex_responses | plugins/model-providers/meta-ai/__init__.py |
| minimax | mini-max | anthropic_messages | plugins/model-providers/minimax/__init__.py |
| minimax-cn | minimax-china, minimax_cn | anthropic_messages | plugins/model-providers/minimax/__init__.py |
| minimax-oauth | minimax_oauth, minimax-portal, minimax-global, minimax-oauth-io | anthropic_messages | plugins/model-providers/minimax/__init__.py |
| nebius-token-factory | nebius, nebius-tokenfactory, nebius-tf, token-factory, tokenfactory | 默认/运行时解析 | plugins/model-providers/nebius-token-factory/__init__.py |
| nous | nous-portal, nousresearch | 默认/运行时解析 | plugins/model-providers/nous/__init__.py |
| novita | novita-ai, novitaai | 默认/运行时解析 | plugins/model-providers/novita/__init__.py |
| nvidia | nvidia-nim, nim, build-nvidia, nemotron | 默认/运行时解析 | plugins/model-providers/nvidia/__init__.py |
| ollama-cloud | ollama_cloud | 默认/运行时解析 | plugins/model-providers/ollama-cloud/__init__.py |
| openai-codex | codex, openai_codex | codex_responses | plugins/model-providers/openai-codex/__init__.py |
| opencode-zen | opencode, opencode_zen, zen | 默认/运行时解析 | plugins/model-providers/opencode-zen/__init__.py |
| opencode-go | opencode_go, go, opencode-go-sub | 默认/运行时解析 | plugins/model-providers/opencode-zen/__init__.py |
| openrouter | or | 默认/运行时解析 | plugins/model-providers/openrouter/__init__.py |
| qwen-oauth | qwen, qwen-portal, qwen-cli | 默认/运行时解析 | plugins/model-providers/qwen-oauth/__init__.py |
| router | ramp-router, ramp, router.com | codex_responses | plugins/model-providers/router/__init__.py |
| stepfun | step, stepfun-coding-plan | 默认/运行时解析 | plugins/model-providers/stepfun/__init__.py |
| upstage | solar | 默认/运行时解析 | plugins/model-providers/upstage/__init__.py |
| vertex | google-vertex, vertex-ai, gcp-vertex, vertexai | chat_completions | plugins/model-providers/vertex/__init__.py |
| xai | grok, x-ai, x.ai | codex_responses | plugins/model-providers/xai/__init__.py |
| xiaomi | mimo, xiaomi-mimo | 默认/运行时解析 | plugins/model-providers/xiaomi/__init__.py |
| zai | glm, z-ai, z.ai, zhipu | 默认/运行时解析 | plugins/model-providers/zai/__init__.py |

## 本地与代理引擎

llama.cpp、vLLM、SGLang、LM Studio、Ollama 的 OpenAI-compatible 路径同用 Chat adapter；Ollama 原生字段单列。LiteLLM、One API / New API、企业自定义网关的呈现依据实际返回协议，不按域名猜测。

## 已知边界

当前 Hermes 的 post_api_request 会为缺失缓存桶填 0，且 response.usage 也是 canonical；因此实时 collector 对零缓存保守隐藏百分比。未关联轮次、缺失 usage、超过有界请求容量均显式显示统计不完整/待采集。辅助调用不计入本轮主请求。

本轮检索用户 fork 无 open issues；上游包括 #116 中途压缩/插话、#114 anchor/sequence、#111/#109 媒体投递、#98 elementID、#82 审批等，现有机制由回归测试保留；这次 footer 重构不把它们标为新增已修复。
