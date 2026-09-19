(()=>{
  const script=document.currentScript;
  const expectedOrigin=script?new URL(script.src,location.href).origin:'';
  function frames(){return [...document.querySelectorAll('iframe[data-ivory-enquiry]')]}
  function resize(event){
    if(expectedOrigin&&event.origin!==expectedOrigin)return;
    if(!event.data||event.data.type!=='ivorydigital:resize')return;
    const frame=frames().find(item=>item.contentWindow===event.source);
    if(!frame)return;
    const height=Math.max(620,Math.min(4000,Number(event.data.height)||1050));
    frame.style.height=`${height}px`;
  }
  addEventListener('message',resize);
  frames().forEach(frame=>{frame.style.width='100%';frame.style.border='0'});
})();
