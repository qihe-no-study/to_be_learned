# 从 LLM 到 Agent Skill，一期视频带你打通底层逻辑！
> https://www.bilibili.com/video/BV1E7wtzaEdq


- LLM: 大语言模型，底层是 transform
- Token: 大模型处理文本的最基本单位
    - 文本转换为token的tokenizer: https://platform.openai.com/tokenizer
- Context: 大模型每次处理任务时接收到的信息总和
    - 大模型没有记忆功能，只是一个概率计算的函数
- Context Window: Context能够容纳的最大token数量
- RAG: https://www.bilibili.com/video/BV1JLN2z4EZQ
- Prompt: 提示词
    - User prompt: 用户输入的提示词
    - System prompt: 后台配置的人设和做事规则
- Tool: 给LLM使用的工具，传递给平台
    - 大模型的问题：无法感知外界环境
- mcp(model context protool): 模型上下文协议
    - https://space.bilibili.com/1815948385/lists/5797984?type=season
- Agent: 能够持续解决问题的LLM系统
    - 能够自主规划，自主调用工具的系统
    - https://www.bilibili.com/video/BV1TSg7zuEqR
- [x] Agent skill: markdown文档
    - name / description / 指令层
    - https://www.bilibili.com/video/BV1cGigBQE6n

# Agent Skill 从使用到原理，一次讲清
> https://www.bilibili.com/video/BV1cGigBQE6n

1. .claude/skills/ 存放
2. 按需加载：LLM选择对应的skill后，才会加载对应的全部内容


高级用法：
- Reference: 按需中的按需加载，会被加载到context，消耗token
- Script: 直接使用脚本，不会读取，无token消耗


潮进武披露 Progressive Disclosure

元数据层 名称/描述 始终加载
指令层 SKILL.md 中除名称和描述之外的内容 按需加载
资源层 Reference/Script 按需中的按需加载

- mcp和skill的使用场景：https://claude.com/blog/skills-explained

# RAG 工作机制详解——一个高质量知识库背后的技术全流程
> Retrieval-Augmented Generation （检索增强生成）

- 数据预处理：分片 -> 索引 -> 召回
    - 索引：1. 通过Embedding 将片段文本转换为向量 2. 将片段文本和片段向量存入向量数据库
        - 含义相近的片段，经过Embedding后，向量相似度较高
        - Embedding模型的选择：https: //huggingface.co/spaces/mteb/
    - 召回: 根据提问的内容，从向量数据库中，匹配topN的向量相似度

