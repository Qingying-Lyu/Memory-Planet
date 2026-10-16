(function (root) {
  const concepts = [
    {
      id: 'image', title: '镜像与容器', subtitle: '从静态模板到运行实例', dimension: '理解', icon: 'layers',
      source: 'Docker · What is a container?', url: 'https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/',
      body: '镜像包含创建容器所需的文件与配置，可以理解为一个只读模板。容器是基于镜像创建的实例，具有自己的运行状态和可写层。一个镜像可以用于创建多个容器。容器停止后仍然存在；停止和删除是不同的生命周期动作。',
      takeaway: '镜像像食谱，容器像按食谱做出的菜；这个类比只帮助区分模板与实例，不能代替具体技术定义。',
      prompts: ['不用类比，解释镜像和容器的区别，以及它们之间的关系。', '同一个镜像能创建多个容器吗？结合模板、实例与状态说明原因。'],
      hint: '从“只读模板”“实例”“一个与多个”三个角度组织回答。',
      criteria: [{text:'镜像提供文件与配置，是用于创建实例的只读模板', weight:40}, {text:'容器是实例，具有运行状态和可写层', weight:40}, {text:'同一镜像可以创建多个容器', weight:20}],
      correct: '镜像是包含文件和配置的只读模板。容器是由镜像创建的实例，具有自己的状态与可写层。同一个镜像可以创建多个容器。',
      partial: '镜像是只读模板，容器是镜像的实例。我还不清楚状态和可写层，一个镜像可以创建多个容器。',
      wrong: '镜像和容器是同一个东西，镜像就是运行中的进程，一个镜像只能对应一个容器。',
      critical: '把镜像与容器认定为同一对象，或坚持一个镜像只能创建一个容器。', related: ['lifecycle', 'isolation']
    },
    {
      id: 'lifecycle', title: '停止与删除', subtitle: '状态变化不等于对象消失', dimension: '理解', icon:'cycle',
      source:'Docker · What is Docker?', url:'https://docs.docker.com/get-started/docker-overview/',
      body:'容器可以处于创建、运行、停止等状态。停止容器意味着它的运行进程停止，并不等于删除这个容器。删除容器会移除该容器及其可写层；持久化存储是否保留取决于存储类型和删除操作，不能把“容器删除”简单等同于“所有数据都消失”。',
      takeaway:'先区分对象是否还存在，再讨论数据放在哪里。',
      prompts:['“容器一停止，容器本身就被删除了。”请纠正，并说明删除对可写层和独立存储的影响。', '停止与删除有什么不同？请同时说明容器对象、可写层和独立存储的去留。'],
      hint:'运行状态与对象存在是两件事；再想想可写层和独立存储。',
      criteria:[{text:'停止意味着运行进程停止，不等于容器对象消失',weight:40},{text:'删除移除容器及其可写层',weight:40},{text:'不能据此断言所有独立持久化数据都被删除',weight:20}],
      correct:'停止只改变运行状态，容器对象仍存在。删除会移除容器及其可写层，但不能据此说所有独立持久化数据也被删除。',
      partial:'停止时容器对象仍然存在，删除则移除容器。我还不清楚可写层，但独立持久化数据不一定消失。',
      wrong:'停止容器就是删除容器，同时所有外部存储的数据都会消失。',
      critical:'认定停止必然删除容器，或容器删除必然清空所有独立存储。',related:['image','storage']
    },
    {
      id:'isolation',title:'容器的隔离',subtitle:'隔离进程，共享内核',dimension:'理解',icon:'shield',
      source:'Docker · What is a container?',url:'https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/',
      body:'以常见 Linux 容器为例，容器以隔离进程的方式运行，共享宿主环境的内核。传统虚拟机通常包含自己的客户操作系统内核。容器隔离不等于绝对安全，也不能推出每个容器自带独立内核。这里讨论常见模型，不覆盖所有特殊运行时。',
      takeaway:'记住比较的范围：常见 Linux 容器与传统虚拟机。',
      prompts:['对比常见 Linux 容器和传统虚拟机与内核的关系，并说明隔离是否保证绝对安全。','“容器有隔离，所以每个容器都有自己的独立内核，而且绝对安全。”请纠正，并与传统虚拟机对比。'],
      hint:'共享内核、客户操作系统、隔离的边界。',
      criteria:[{text:'常见 Linux 容器共享宿主环境内核',weight:40},{text:'传统虚拟机通常具有自己的客户操作系统内核',weight:40},{text:'隔离不能保证绝对安全，比较有适用范围',weight:20}],
      correct:'常见 Linux 容器通过隔离进程运行，共享宿主环境的内核；传统虚拟机通常有自己的客户操作系统内核。隔离不等于绝对安全。',
      partial:'常见 Linux 容器共享宿主内核，虚拟机有自己的操作系统，但我不确定内核的差异。隔离不意味着绝对安全。',
      wrong:'每个 Linux 容器都有独立内核，所以容器隔离能保证绝对安全。',
      critical:'认定常见 Linux 容器各有独立内核，或隔离保证绝对安全。',related:['image','resource']
    },
    {
      id:'storage',title:'数据与生命周期',subtitle:'数据去留取决于存放位置',dimension:'关联',icon:'database',
      source:'Docker · Storage',url:'https://docs.docker.com/engine/storage/',
      body:'容器可写层与该容器生命周期绑定。卷是 Docker 管理的存储，其生命周期可独立于容器。因而讨论容器删除后的数据时，需要区分数据在容器可写层、卷还是其他挂载位置，也需要确认操作是否同时删除卷。',
      takeaway:'“容器删了，数据还在吗？”需要先问：数据存在哪里、执行了什么删除操作？',
      prompts:['为什么不能只凭“删除了容器”判断数据是否还在？','把容器生命周期与数据生命周期联系起来，说明可写层和卷的不同。'],
      hint:'可写层跟随容器，卷可以有独立生命周期，但也可以被单独删除。',
      criteria:[{text:'容器可写层随该容器删除而移除',weight:40},{text:'卷的生命周期可独立于容器',weight:40},{text:'最终结果取决于存储位置及是否同时删除卷',weight:20}],
      correct:'容器可写层随容器删除而移除；卷的生命周期可以独立于容器。要判断数据去留，需要知道存储位置和删除操作是否同时删除卷。',
      partial:'可写层跟容器一起删除，卷与容器有所区别，但我不清楚生命周期。结果还要看存放位置和删除动作。',
      wrong:'容器删除后所有卷必然自动消失，所有数据一定被清空。',
      critical:'认定删除容器必然删除全部卷和所有独立数据。',related:['lifecycle']
    },
    {
      id:'port',title:'端口与映射',subtitle:'宿主入口与容器内端口',dimension:'记忆',icon:'link',
      source:'Docker · Publishing ports',url:'https://docs.docker.com/get-started/docker-concepts/running-containers/publishing-ports/',
      body:'端口发布将宿主机的某个端口映射到容器的某个端口。两端端口号不必相同。可访问性还受监听地址、防火墙和网络环境影响，不能仅凭配置了端口映射就断言所有客户端都能访问。',
      takeaway:'区分“映射到哪里”与“谁实际能够访问”。',
      prompts:['回忆端口发布连接的是哪两端，端口号是否必须相同，并说出一个访问限制条件。','解释宿主机端口与容器端口的关系、号码是否须一致，并说出一个访问限制条件。'],
      hint:'宿主端口 → 容器端口；数字可不同；还要考虑网络条件。',
      criteria:[{text:'描述宿主机端口映射到容器端口',weight:40},{text:'两端端口号不必相同',weight:40},{text:'访问还取决于监听地址、防火墙等环境',weight:20}],
      correct:'端口发布将宿主机端口映射到容器端口，两端端口号不必相同。实际访问还受监听地址、防火墙等条件影响。',
      partial:'它连接宿主机与容器的端口，但我不确定数字是否必须相同。实际访问还受防火墙影响。',
      wrong:'两端端口号必须相同，只要发布端口就保证任何客户端都能访问。',
      critical:'认定端口号必须相同，或端口发布保证所有客户端都能访问。',related:['isolation']
    },
    {
      id:'resource',title:'隔离与资源限制',subtitle:'隔离不自动等于限额',dimension:'理解',icon:'sliders',
      source:'Docker · Resource constraints',url:'https://docs.docker.com/engine/containers/resource_constraints/',
      body:'容器隔离与 CPU、内存资源限制是相关但不同的概念。不能因为应用运行在容器里，就推断已经为它设置了固定 CPU 和内存上限。讨论资源约束时，需要查明实际配置与运行环境。',
      takeaway:'不要把“具备限制能力”误认为“已经设置了限制”。',
      prompts:['“应用放进容器后，就自动拥有固定的 CPU 和内存上限。”这个推论为什么不成立？','容器隔离与资源限制是什么关系？为什么还要看具体配置？'],
      hint:'隔离与限额分开理解；能力与配置分开理解。',
      criteria:[{text:'隔离与资源限制是不同概念',weight:40},{text:'容器化不意味着已配置固定 CPU/内存上限',weight:40},{text:'应根据实际配置和环境判断约束',weight:20}],
      correct:'隔离和资源限制是不同概念。应用进入容器并不意味着已经设置固定 CPU 和内存上限，需要根据配置和运行环境判断。',
      partial:'隔离不同于资源限制，但我不确定是否自动有限额。应该查看实际配置。',
      wrong:'只要放进容器，就自动设置了固定 CPU 和内存上限，无需查看配置。',
      critical:'认定运行在容器中必然已经设置固定 CPU/内存上限。',related:['isolation']
    }
  ];
  root.DemoContent = concepts;
  if (typeof module !== 'undefined') module.exports = concepts;
})(typeof window !== 'undefined' ? window : globalThis);
